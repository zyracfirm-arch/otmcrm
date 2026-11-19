import json
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.models import User
from django.db import transaction
from django.http import JsonResponse, Http404
from django.shortcuts import render, get_object_or_404, redirect
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST
from django.db.models import Q
from django.contrib.auth.models import User, Group
from .models import Category, MenuItem, Customer, Coupon, Order, OrderItem, ShopSettings
from .security import shop_device_and_login_required


# ==========================================
# 1. PUBLIC WEBSITE VIEWS (Open to Customers)
# ==========================================
def public_menu(request):
    categories = Category.objects.prefetch_related('items').all()
    coupons = Coupon.objects.filter(is_active=True)
    veg_count = MenuItem.objects.filter(is_veg=True, is_available=True).count()
    nonveg_count = MenuItem.objects.filter(is_veg=False, is_available=True).count()
    shop_settings = ShopSettings.get_solo()

    return render(request, 'public_menu.html', {
        'categories': categories,
        'coupons': coupons,
        'veg_count': veg_count,
        'nonveg_count': nonveg_count,
        'shop_settings': shop_settings,
    })

def about_page(request):
    shop_settings = ShopSettings.get_solo()
    return render(request, 'about.html', {
        'shop_settings': shop_settings,
    })


@require_POST
def api_validate_coupon(request):
    try:
        data = json.loads(request.body)
    except Exception:
        return JsonResponse({'valid': False, 'message': 'Invalid request.'}, status=400)

    code = (data.get('code') or '').strip().upper()
    try:
        subtotal = Decimal(str(data.get('subtotal', 0)))
    except (InvalidOperation, TypeError, ValueError):
        subtotal = Decimal('0.00')

    phone = (data.get('phone') or '').strip()

    try:
        coupon = Coupon.objects.get(code__iexact=code, is_active=True)
    except Coupon.DoesNotExist:
        return JsonResponse({'valid': False, 'message': 'Invalid or expired coupon code.'})

    if subtotal < coupon.min_order_amount:
        return JsonResponse({'valid': False, 'message': f'Minimum order of ₹{coupon.min_order_amount:.0f} required.'})

    if coupon.first_order_only and phone:
        if Customer.objects.filter(phone=phone, total_orders__gt=0).exists():
            return JsonResponse({'valid': False, 'message': 'Valid on first-time orders only.'})

    if coupon.discount_type == 'FLAT':
        discount = coupon.value
    else:
        discount = min((subtotal * coupon.value) / Decimal('100.00'), coupon.max_discount)

    discount = min(discount, subtotal)
    return JsonResponse({
        'valid': True,
        'code': coupon.code,
        'discount': float(discount),
        'final_total': float(subtotal - discount),
        'message': f'₹{discount:.0f} discount applied!'
    })


@require_POST
def api_place_order(request):
    shop_settings = ShopSettings.get_solo()
    if not shop_settings.is_open:
        return JsonResponse({
            'success': False,
            'shop_closed': True,
            'error': f"{shop_settings.closed_reason or 'Kitchen is resting'} — opens at {shop_settings.reopen_time}."
        }, status=400)
        
    if not request.user.is_authenticated or request.user.is_staff:
        return JsonResponse({
            'success': False,
            'auth_required': True,
            'error': 'Please sign in as a customer first.'
        })

    try:
        data = json.loads(request.body)
    except Exception:
        return JsonResponse({'success': False, 'error': 'Invalid request payload.'}, status=400)

    cart = data.get('cart', [])
    if not cart:
        return JsonResponse({'success': False, 'error': 'Your tray is empty.'}, status=400)

    order_type = data.get('order_type', 'DELIVERY')
    if order_type not in ('DELIVERY', 'PICKUP'):
        order_type = 'DELIVERY'

    address = (data.get('address') or '').strip()
    coupon_code = (data.get('coupon_code') or '').strip().upper()

    if order_type == 'DELIVERY' and not address:
        return JsonResponse({'success': False, 'error': 'Please enter your delivery address.'}, status=400)

    with transaction.atomic():
        customer = getattr(request.user, 'customer_profile', None)
        if not customer:
            customer, _ = Customer.objects.get_or_create(
                user=request.user,
                defaults={
                    'phone': request.user.username,
                    'name': request.user.first_name or 'Guest'
                }
            )

        if address and order_type == 'DELIVERY':
            customer.address = address
            customer.save(update_fields=['address'])

        # Validate cart items (supports Pure Veg, Non-Veg, and Mixed orders)
        validated_items = []
        subtotal = Decimal('0.00')

        for entry in cart:
            item_id = entry.get('id')
            try:
                qty = int(entry.get('qty', 1))
            except (TypeError, ValueError):
                qty = 1

            if qty <= 0:
                continue

            db_item = MenuItem.objects.filter(id=item_id, is_available=True).first()
            if not db_item:
                return JsonResponse(
                    {'success': False, 'error': f"Item '{entry.get('name', 'Selected item')}' is currently sold out."},
                    status=400
                )

            subtotal += db_item.price * qty
            validated_items.append((db_item, qty))

        if not validated_items:
            return JsonResponse({'success': False, 'error': 'No valid available items in tray.'}, status=400)

        # Validate & apply Promo Coupon if provided
        discount_amount = Decimal('0.00')
        applied_code = ''
        if coupon_code:
            coupon = Coupon.objects.select_for_update().filter(code__iexact=coupon_code, is_active=True).first()
            if coupon and subtotal >= coupon.min_order_amount:
                if not (coupon.first_order_only and customer.total_orders > 0):
                    if coupon.discount_type == 'FLAT':
                        discount_amount = min(coupon.value, subtotal)
                    else:
                        raw_disc = (subtotal * coupon.value) / Decimal('100.00')
                        discount_amount = min(raw_disc, coupon.max_discount, subtotal)
                    applied_code = coupon.code
                    coupon.used_count += 1
                    coupon.save(update_fields=['used_count'])

        total_amount = max(subtotal - discount_amount, Decimal('0.00'))

        # Create Order
        order = Order.objects.create(
            customer=customer,
            customer_name=customer.name,
            customer_phone=customer.phone,
            delivery_address=address if order_type == 'DELIVERY' else 'Self Pickup at Khamasa Counter',
            order_type=order_type,
            subtotal=subtotal,
            discount_amount=discount_amount,
            total_amount=total_amount,
            coupon_code=applied_code,
            status='NEW',
        )

        # Create OrderItems matching core/models.py fields
        for db_item, qty in validated_items:
            OrderItem.objects.create(
                order=order,
                item_name=db_item.name,
                unit_price=db_item.price,
                quantity=qty,
                is_veg=db_item.is_veg,
            )

        # Update Customer CRM lifetime metrics
        customer.total_orders += 1
        customer.total_spent += total_amount
        customer.save(update_fields=['total_orders', 'total_spent', 'last_order_at'])

    return JsonResponse({
        'success': True,
        'order_number': order.order_number,
        'tracking_url': reverse('track_order', args=[order.tracking_token]),
    })


def track_order(request, token):
    order = get_object_or_404(Order.objects.prefetch_related('items'), tracking_token=token)
    
    if request.headers.get('x-requested-with') == 'XMLHttpRequest':
        return JsonResponse({
            'status': order.status,
            'status_label': order.get_status_display(),
            'rider_name': order.delivery_boy.username.title() if order.delivery_boy else None
        })
    
    return render(request, 'track_order.html', {'order': order})


# ==========================================
# 2. SHOP DEVICE ACTIVATION & CRM VIEWS
# ==========================================
def activate_shop_device(request):
    """One-time URL opened ONLY on the Shop PC/Tablet & Owner's Phone to authorize the device."""
    if request.method == 'POST':
        pin = (request.POST.get('secret_pin') or '').strip()
        if pin == settings.SHOP_DEVICE_SECRET:
            response = redirect('crm_login')
            response.set_signed_cookie(
                'otm_shop_device',
                settings.SHOP_DEVICE_SECRET,
                salt='zyrac_lock',
                max_age=31536000,
                httponly=True,
                samesite='Strict'
            )
            return response
    return render(request, 'device_activate.html')


def crm_login(request):
    device_token = request.get_signed_cookie('otm_shop_device', default=None, salt='zyrac_lock')
    if device_token != settings.SHOP_DEVICE_SECRET:
        raise Http404("Page not found")

    error = None
    if request.method == 'POST':
        u = (request.POST.get('username') or '').strip()
        p = request.POST.get('password') or ''
        user = authenticate(request, username=u, password=p)
        if user and user.is_staff:
            login(request, user)
            if user.is_superuser or user.username == 'kitchen':
                return redirect('crm_orders_inbox')
            return redirect('delivery_dashboard')
                
        error = "Invalid staff credentials"
    return render(request, 'crm_login.html', {'error': error})


@shop_device_and_login_required(owner_only=False)
def crm_orders_inbox(request):
    today = timezone.localdate()
    orders = Order.objects.exclude(status__in=['DONE', 'CANCELLED']).select_related('delivery_boy').prefetch_related('items').order_by('created_at')
    history_orders = Order.objects.filter(status__in=['DONE', 'CANCELLED']).select_related('delivery_boy').prefetch_related('items').order_by('-updated_at')[:500]
    riders = User.objects.filter(is_staff=True, is_superuser=False).exclude(username='kitchen')

    return render(request, 'crm_dashboard.html', {
        'orders': orders,
        'history_orders': history_orders,
        'today_str': today.strftime('%Y-%m-%d'),
        'riders': riders,
        'is_owner': request.user.is_superuser,
    })


@shop_device_and_login_required(owner_only=False)
@require_POST
def api_assign_delivery(request, order_id):
    order = get_object_or_404(Order, id=order_id)
    try:
        rider_id = json.loads(request.body).get('rider_id')
    except Exception:
        return JsonResponse({'success': False, 'error': 'Invalid payload'}, status=400)

    if rider_id:
        rider = get_object_or_404(User, id=rider_id, is_staff=True)
        order.delivery_boy = rider
        order.status = 'OUT'
        order.save(update_fields=['delivery_boy', 'status', 'updated_at'])
    return JsonResponse({'success': True})


@shop_device_and_login_required(owner_only=False)
def delivery_dashboard(request):
    orders = Order.objects.filter(
        delivery_boy=request.user,
        status='OUT'
    ).prefetch_related('items').order_by('-updated_at')

    history_orders = Order.objects.filter(
        delivery_boy=request.user,
        status='DONE'
    ).prefetch_related('items').order_by('-updated_at')[:100]

    return render(request, 'delivery_dashboard.html', {
        'orders': orders,
        'history_orders': history_orders,
    })


@shop_device_and_login_required(owner_only=False)
@require_POST
def api_update_order_status(request, order_id):
    order = get_object_or_404(Order, id=order_id)
    try:
        data = json.loads(request.body or '{}')
    except Exception:
        return JsonResponse({'success': False, 'error': 'Invalid payload'}, status=400)

    new_status = data.get('status')
    payment_mode = (data.get('payment_mode') or '').strip().upper()

    valid_keys = dict(Order.STATUS_CHOICES).keys()
    if new_status == 'PREPARING' and 'PREP' in valid_keys:
        new_status = 'PREP'
    elif new_status == 'CANCEL' and 'CANCELLED' in valid_keys:
        new_status = 'CANCELLED'

    update_fields = ['updated_at']

    if new_status in valid_keys:
        order.status = new_status
        update_fields.append('status')

    if payment_mode in ['CASH', 'UPI', 'UNPAID']:
        order.payment_mode = payment_mode
        update_fields.append('payment_mode')

    if len(update_fields) > 1:
        order.save(update_fields=update_fields)

    return JsonResponse({
        'success': True,
        'status': order.status,
        'payment_mode': getattr(order, 'payment_mode', 'UNPAID'),
    })


@shop_device_and_login_required(owner_only=True)
def crm_owner_portal(request):
    today = timezone.now()
    local_today = timezone.localtime(today)
    today_str = local_today.strftime('%Y-%m-%d')
    thirty_days_ago = today - timedelta(days=30)

    done_orders = Order.objects.filter(
        Q(status='DONE') & (Q(created_at__gte=thirty_days_ago) | Q(updated_at__gte=thirty_days_ago))
    ).select_related('delivery_boy').prefetch_related('items')

    orders_data = []
    for o in done_orders:
        created_local = timezone.localtime(o.created_at)
        updated_local = timezone.localtime(o.updated_at) if o.updated_at else created_local
        raw_mode = (getattr(o, 'payment_mode', '') or 'CASH').strip().upper()
        normalized_mode = 'UPI' if raw_mode == 'UPI' else 'CASH'

        orders_data.append({
            'date': created_local.strftime('%Y-%m-%d'),
            'updated_date': updated_local.strftime('%Y-%m-%d'),
            'hour': created_local.hour,
            'amount': float(o.total_amount),
            'rider': o.delivery_boy.username.title() if o.delivery_boy else 'Counter / Pickup',
            'payment_mode': normalized_mode,
            'items': [
                {
                    'name': i.item_name,
                    'qty': i.quantity,
                    'price': float(i.unit_price),
                    'is_veg': getattr(i, 'is_veg', getattr(getattr(i, 'menu_item', None), 'is_veg', True)),
                }
                for i in o.items.all()
            ]
        })

    today_cancelled_qs = Order.objects.filter(
        Q(status='CANCELLED') & (Q(updated_at__date=local_today) | Q(created_at__date=local_today))
    )
    today_cancelled_count = today_cancelled_qs.count()
    today_cancelled_value = sum(float(o.total_amount) for o in today_cancelled_qs)

    all_history = Order.objects.select_related('delivery_boy', 'customer').prefetch_related('items').order_by('-created_at')[:1000]
    customers = Customer.objects.order_by('-total_spent', '-total_orders')

    premium_count = Customer.objects.filter(total_spent__gte=Decimal('5000.00')).count()
    normal_count = Customer.objects.filter(total_spent__lt=Decimal('5000.00'), total_orders__gte=2).count()
    new_member_count = Customer.objects.filter(total_spent__lt=Decimal('5000.00'), total_orders__lte=1).count()

    # Fetch Riders & Kitchen Staff (exclude owner/superusers)
    staff_users = User.objects.filter(is_staff=True, is_superuser=False).prefetch_related('groups').order_by('-date_joined')
    staff_members = []
    for u in staff_users:
        group_names = [g.name.lower() for g in u.groups.all()]
        role = 'RIDER' if ('rider' in group_names or 'delivery' in group_names) else 'KITCHEN'
        staff_members.append({
            'id': u.id,
            'username': u.username,
            'role': role,
            'joined': timezone.localtime(u.date_joined).strftime('%d %b %Y'),
        })

    return render(request, 'crm_owner.html', {
        'orders_json': json.dumps(orders_data),
        'all_history': all_history,
        'today_str': today_str,
        'today_cancelled_count': today_cancelled_count,
        'today_cancelled_value': round(today_cancelled_value),
        'customers': customers,
        'premium_count': premium_count,
        'normal_count': normal_count,
        'new_member_count': new_member_count,
        'menu_items': MenuItem.objects.select_related('category').all(),
        'categories': Category.objects.all(),
        'coupons': Coupon.objects.all(),
        'shop_settings': ShopSettings.get_solo(),
        'staff_members': staff_members,
    })


@shop_device_and_login_required(owner_only=True)
@require_POST
def api_create_staff(request):
    try:
        data = json.loads(request.body or '{}')
    except Exception:
        return JsonResponse({'success': False, 'error': 'Invalid payload'}, status=400)

    username = (data.get('username') or '').strip()
    password = (data.get('password') or '').strip()
    role = (data.get('role') or 'RIDER').strip().upper()

    if not username or not password:
        return JsonResponse({'success': False, 'error': 'Username and password are required.'}, status=400)
    if len(password) < 4:
        return JsonResponse({'success': False, 'error': 'Password must be at least 4 characters.'}, status=400)
    if User.objects.filter(username__iexact=username).exists():
        return JsonResponse({'success': False, 'error': 'That username already exists.'}, status=400)

    user = User.objects.create_user(username=username, password=password)
    user.is_staff = True
    user.is_superuser = False
    user.save()

    group_name = 'Rider' if role == 'RIDER' else 'Kitchen'
    group, _ = Group.objects.get_or_create(name=group_name)
    user.groups.add(group)

    return JsonResponse({
        'success': True,
        'id': user.id,
        'username': user.username,
        'role': 'RIDER' if role == 'RIDER' else 'KITCHEN',
        'joined': timezone.localtime(user.date_joined).strftime('%d %b %Y'),
    })


@shop_device_and_login_required(owner_only=True)
@require_POST
def api_delete_staff(request, user_id):
    user = get_object_or_404(User, id=user_id)
    if user.is_superuser or user.id == request.user.id:
        return JsonResponse({'success': False, 'error': 'Cannot delete owner account.'}, status=403)

    user.delete()
    return JsonResponse({'success': True})


@shop_device_and_login_required(owner_only=True)
@require_POST
def api_toggle_shop_status(request):
    try:
        data = json.loads(request.body)
    except Exception:
        data = {}

    shop = ShopSettings.get_solo()
    if 'is_open' in data:
        shop.is_open = bool(data['is_open'])
    else:
        shop.is_open = not shop.is_open

    if 'reopen_time' in data and str(data['reopen_time']).strip():
        shop.reopen_time = str(data['reopen_time']).strip()[:50]

    if 'closed_reason' in data and str(data['closed_reason']).strip():
        shop.closed_reason = str(data['closed_reason']).strip()[:120]

    shop.save()
    return JsonResponse({
        'success': True,
        'is_open': shop.is_open,
        'reopen_time': shop.reopen_time,
        'closed_reason': shop.closed_reason,
    })

@shop_device_and_login_required(owner_only=True)
@require_POST
def api_toggle_menu_item(request, item_id):
    item = get_object_or_404(MenuItem, id=item_id)
    item.is_available = not item.is_available
    item.save(update_fields=['is_available'])
    return JsonResponse({'success': True, 'is_available': item.is_available})


@shop_device_and_login_required(owner_only=True)
@require_POST
def api_save_menu_item(request):
    try:
        data = json.loads(request.body)
    except Exception:
        return JsonResponse({'success': False, 'error': 'Invalid payload'}, status=400)

    item_id = data.get('id')
    if item_id:
        item = get_object_or_404(MenuItem, id=item_id)
        if 'name' in data and str(data['name']).strip():
            item.name = str(data['name']).strip()
        if 'price' in data and str(data['price']).strip():
            try:
                item.price = Decimal(str(data['price']))
            except (InvalidOperation, TypeError, ValueError):
                return JsonResponse({'success': False, 'error': 'Invalid price'}, status=400)
        if 'is_veg' in data:
            item.is_veg = bool(data['is_veg'])
        item.save()
        return JsonResponse({'success': True})

    name = (data.get('name') or '').strip()
    if not name or not str(data.get('price', '')).strip():
        return JsonResponse({'success': False, 'error': 'Dish name and price are required'}, status=400)

    try:
        price = Decimal(str(data['price']))
    except (InvalidOperation, TypeError, ValueError):
        return JsonResponse({'success': False, 'error': 'Invalid price format'}, status=400)

    cat_name = (data.get('category') or 'Signatures').strip() or 'Signatures'
    category, _ = Category.objects.get_or_create(name=cat_name)
    MenuItem.objects.create(
        category=category,
        name=name,
        price=price,
        description=(data.get('description') or '').strip(),
        image_url=(data.get('image_url') or '').strip(),
        is_veg=bool(data.get('is_veg', True)),
        is_bestseller=bool(data.get('is_bestseller', False)),
    )
    return JsonResponse({'success': True})


@shop_device_and_login_required(owner_only=True)
@require_POST
def api_create_coupon(request):
    try:
        data = json.loads(request.body)
    except Exception:
        return JsonResponse({'success': False, 'error': 'Invalid payload'}, status=400)

    code = (data.get('code') or '').strip().upper()
    if not code:
        return JsonResponse({'success': False, 'error': 'Coupon code is required'}, status=400)

    try:
        val = Decimal(str(data['value']))
        min_amt = Decimal(str(data.get('min_order_amount', 0) or 0))
        max_disc = Decimal(str(data.get('max_discount', 100) or 100))
    except (InvalidOperation, TypeError, ValueError, KeyError):
        return JsonResponse({'success': False, 'error': 'Invalid numeric coupon values'}, status=400)

    Coupon.objects.update_or_create(
        code=code,
        defaults={
            'discount_type': data.get('discount_type', 'FLAT'),
            'value': val,
            'min_order_amount': min_amt,
            'max_discount': max_disc,
            'first_order_only': bool(data.get('first_order_only', False)),
            'is_active': True
        }
    )
    return JsonResponse({'success': True})


@shop_device_and_login_required(owner_only=True)
@require_POST
def api_toggle_coupon(request, coupon_id):
    coupon = get_object_or_404(Coupon, id=coupon_id)
    coupon.is_active = not coupon.is_active
    coupon.save(update_fields=['is_active'])
    return JsonResponse({'success': True, 'is_active': coupon.is_active})


def crm_logout(request):
    was_staff = request.user.is_authenticated and request.user.is_staff
    logout(request)
    if was_staff:
        return redirect('crm_login')
    return redirect('public_menu')


@require_POST
def api_customer_auth(request):
    try:
        data = json.loads(request.body)
    except Exception:
        return JsonResponse({'success': False, 'error': 'Invalid request.'}, status=400)

    phone = (data.get('phone') or '').strip()
    pin = (data.get('pin') or '').strip()
    name = (data.get('name') or '').strip()
    action = data.get('action')

    if not phone or not pin:
        return JsonResponse({'success': False, 'error': 'Phone and PIN are required.'})

    if action == 'signup':
        if User.objects.filter(username=phone).exists():
            return JsonResponse({'success': False, 'error': 'Number already registered. Please login.'})
        
        with transaction.atomic():
            user = User.objects.create_user(username=phone, password=pin)
            customer = Customer.objects.filter(phone=phone).first()
            if customer:
                customer.user = user
                if name:
                    customer.name = name
                customer.save()
                login(request, user)
                return JsonResponse({'success': True, 'name': customer.name})
            else:
                guest_name = name or 'Guest'
                Customer.objects.create(user=user, phone=phone, name=guest_name)
                login(request, user)
                return JsonResponse({'success': True, 'name': guest_name})

    elif action == 'login':
        user = authenticate(request, username=phone, password=pin)
        if user is not None and not user.is_staff:
            login(request, user)
            customer, _ = Customer.objects.get_or_create(
                user=user,
                defaults={'phone': phone, 'name': user.first_name or 'Guest'}
            )
            return JsonResponse({'success': True, 'name': customer.name})
        return JsonResponse({'success': False, 'error': 'Invalid Phone Number or PIN.'})

    return JsonResponse({'success': False, 'error': 'Invalid authentication action.'}, status=400)


def customer_dashboard(request):
    if not request.user.is_authenticated or request.user.is_staff:
        return redirect('public_menu')

    customer, _ = Customer.objects.get_or_create(
        user=request.user,
        defaults={'phone': request.user.username, 'name': request.user.first_name or 'Guest'}
    )
    orders = Order.objects.filter(customer=customer).prefetch_related('items').order_by('-created_at')
    return render(request, 'customer_dashboard.html', {'orders': orders})



@shop_device_and_login_required(owner_only=False)
def api_kds_poll(request):
    live_orders = Order.objects.exclude(status__in=['DONE', 'CANCELLED'])
    latest_id = live_orders.order_by('-id').values_list('id', flat=True).first() or 0
    return JsonResponse({
        'latest_id': latest_id,
        'new_count': live_orders.filter(status='NEW').count(),
        'live_count': live_orders.count(),
    })