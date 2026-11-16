import json
from decimal import Decimal
from django.conf import settings
from django.contrib.auth import authenticate, login, logout
from django.db.models import Sum, Count
from django.db.models.functions import ExtractHour
from django.http import JsonResponse, Http404
from django.shortcuts import render, get_object_or_404, redirect
from django.utils import timezone
from django.views.decorators.http import require_POST
from .models import Category, MenuItem, Customer, Coupon, Order, OrderItem
from .security import shop_device_and_login_required
from django.contrib.auth.models import User
from django.db import transaction

# ==========================================
# 1. PUBLIC WEBSITE VIEWS (Open to Customers)
# ==========================================
def public_menu(request):
    categories = Category.objects.prefetch_related('items').all()
    active_coupons = Coupon.objects.filter(is_active=True)
    return render(request, 'public_menu.html', {
        'categories': categories,
        'coupons': active_coupons,
    })

@require_POST
def api_validate_coupon(request):
    data = json.loads(request.body)
    code = data.get('code', '').strip().upper()
    subtotal = Decimal(str(data.get('subtotal', 0)))
    phone = data.get('phone', '').strip()

    try:
        coupon = Coupon.objects.get(code=code, is_active=True)
    except Coupon.DoesNotExist:
        return JsonResponse({'valid': False, 'message': 'Invalid or expired coupon code.'})

    if subtotal < coupon.min_order_amount:
        return JsonResponse({'valid': False, 'message': f'Minimum order of ₹{coupon.min_order_amount} required.'})

    if coupon.first_order_only and phone:
        if Customer.objects.filter(phone=phone, total_orders__gt=0).exists():
            return JsonResponse({'valid': False, 'message': 'Valid on first-time orders only.'})

    if coupon.discount_type == 'FLAT':
        discount = coupon.value
    else:
        discount = min((subtotal * coupon.value) / Decimal('100'), coupon.max_discount)

    discount = min(discount, subtotal)
    return JsonResponse({
        'valid': True,
        'code': coupon.code,
        'discount': float(discount),
        'final_total': float(subtotal - discount),
        'message': f'₹{discount} discount applied!'
    })

@require_POST
def api_place_order(request):
    # 1. Enforce Customer Login (Blocks unauthenticated users and staff)
    if not request.user.is_authenticated or request.user.is_staff:
        return JsonResponse({'error': 'AUTH_REQUIRED'}, status=401)

    data = json.loads(request.body)
    cart = data.get('cart', [])
    if not cart:
        return JsonResponse({'error': 'Cart is empty'}, status=400)

    address = data.get('address', '').strip()
    order_type = data.get('order_type', 'DELIVERY')
    coupon_code = data.get('coupon_code', '').strip().upper()

    # 2. Calculate true subtotal from database prices (Prevents frontend tampering)
    subtotal = Decimal('0.00')
    verified_items = []
    for entry in cart:
        item = get_object_or_404(MenuItem, id=entry['id'], is_available=True)
        qty = int(entry['qty'])
        if qty > 0:
            subtotal += item.price * qty
            verified_items.append((item, qty))

    # 3. Re-verify coupon securely on the server
    discount = Decimal('0.00')
    customer_profile = request.user.customer_profile

    if coupon_code:
        coupon = Coupon.objects.filter(code=coupon_code, is_active=True).first()
        if coupon and subtotal >= coupon.min_order_amount:
            # Enforce First-Order-Only rule
            if not (coupon.first_order_only and customer_profile.total_orders > 0):
                if coupon.discount_type == 'FLAT':
                    discount = coupon.value
                else:
                    discount = min((subtotal * coupon.value) / 100, coupon.max_discount)
                
                # Increment coupon usage count
                coupon.used_count += 1
                coupon.save()

    total_amount = max(subtotal - discount, Decimal('0.00'))

    # 4. Atomic Database Transaction
    # Locks the database for exact milliseconds to prevent collisions and broken data
    with transaction.atomic():
        # Update Customer CRM record
        if address:
            customer_profile.address = address
        customer_profile.total_orders += 1
        
        # SQLite Fix: Safely convert float to Decimal before adding new amount
        customer_profile.total_spent = Decimal(str(customer_profile.total_spent)) + total_amount
        customer_profile.save()

        # Create the Order
        # (The secure OTM-YYMMDD-HHMMSS-RANDOM order_number generates automatically in models.py)
        order = Order.objects.create(
            customer=customer_profile,
            customer_name=customer_profile.name,
            customer_phone=customer_profile.phone,
            delivery_address=customer_profile.address,
            order_type=order_type,
            subtotal=subtotal,
            coupon_code=coupon_code if discount > 0 else '',
            discount_amount=discount,
            total_amount=total_amount
        )

        # Create Order Items
        for item, qty in verified_items:
            OrderItem.objects.create(
                order=order,
                item_name=item.name,
                unit_price=item.price,
                quantity=qty
            )

    # 5. Return success and auto-redirect to live tracker
    return JsonResponse({
        'success': True,
        'order_number': order.order_number,
        'tracking_url': f'/track/{order.tracking_token}/'
    })

def track_order(request, token):
    # Fetch the order securely using the UUID token in the URL
    order = get_object_or_404(Order, tracking_token=token)
    
    # If the JavaScript tracker is polling for a live update (every 5 seconds)
    if request.headers.get('x-requested-with') == 'XMLHttpRequest':
        return JsonResponse({'status': order.status, 'status_label': order.get_status_display()})
    
    # Otherwise, render the full tracking page for the customer
    return render(request, 'track_order.html', {'order': order})
# ==========================================
# 2. SHOP DEVICE ACTIVATION & CRM VIEWS
# ==========================================
def activate_shop_device(request):
    """One-time URL opened ONLY on the Shop PC/Tablet & Owner's Phone to authorize the device."""
    if request.method == 'POST':
        pin = request.POST.get('secret_pin')
        if pin == settings.SHOP_DEVICE_SECRET:
            response = redirect('crm_login')
            # Sets a 1-year encrypted hardware cookie on the shop system
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
        u = request.POST.get('username')
        p = request.POST.get('password')
        user = authenticate(request, username=u, password=p)
        if user and user.is_staff:
            login(request, user)
            
            # ROUTING LOGIC: Owners and Kitchen go to Inbox, Riders go to Delivery Dashboard
            if user.is_superuser or user.username == 'kitchen':
                return redirect('crm_orders_inbox')
            else:
                return redirect('delivery_dashboard')
                
        error = "Invalid staff credentials"
    return render(request, 'crm_login.html', {'error': error})

# 1. Update your existing crm_orders_inbox to pass the 'riders' list to the template
@shop_device_and_login_required(owner_only=False)
def crm_orders_inbox(request):
    today = timezone.localdate()
    orders = Order.objects.filter(created_at__date=today).exclude(status='CANCELLED').prefetch_related('items').order_by('-created_at')
    
    # Get all staff users who are not owners and not the main 'kitchen' account
    riders = User.objects.filter(is_staff=True, is_superuser=False).exclude(username='kitchen')
    
    return render(request, 'crm_dashboard.html', {
        'orders': orders,
        'riders': riders,
        'is_owner': request.user.is_superuser,
        'active_tab': 'inbox'
    })

# 2. Add this NEW function to handle assigning riders
@shop_device_and_login_required(owner_only=False)
@require_POST
def api_assign_delivery(request, order_id):
    order = get_object_or_404(Order, id=order_id)
    rider_id = json.loads(request.body).get('rider_id')
    if rider_id:
        rider = get_object_or_404(User, id=rider_id)
        order.delivery_boy = rider
        order.status = 'OUT' # Automatically moves to Out for Delivery column
        order.save()
    return JsonResponse({'success': True})

# 3. Add this NEW function for the Rider's Mobile Dashboard
@shop_device_and_login_required(owner_only=False)
def delivery_dashboard(request):
    # Only fetch active orders assigned to the logged-in delivery boy
    active_deliveries = Order.objects.filter(delivery_boy=request.user, status='OUT').order_by('-created_at')
    return render(request, 'delivery_dashboard.html', {
        'orders': active_deliveries
    })

@shop_device_and_login_required(owner_only=False)
@require_POST
def api_update_order_status(request, order_id):
    order = get_object_or_404(Order, id=order_id)
    new_status = json.loads(request.body).get('status')
    if new_status in dict(Order.STATUS_CHOICES):
        order.status = new_status
        order.save()
    return JsonResponse({'success': True, 'status': order.status})

@shop_device_and_login_required(owner_only=True)
def crm_owner_portal(request):
    """Strictly accessible ONLY by Ubedullah (Owner)."""
    today = timezone.localdate()
    todays_orders = Order.objects.filter(created_at__date=today, status='DONE')

    daily_revenue = todays_orders.aggregate(total=Sum('total_amount'))['total'] or 0
    daily_count = Order.objects.filter(created_at__date=today).count()

    bestsellers = (
        OrderItem.objects.filter(order__created_at__date=today)
        .values('item_name')
        .annotate(qty=Sum('quantity'))
        .order_by('-qty')[:5]
    )

    busiest_hours = (
        Order.objects.filter(created_at__date=today)
        .annotate(hour=ExtractHour('created_at'))
        .values('hour')
        .annotate(count=Count('id'))
        .order_by('-count')[:4]
    )

    return render(request, 'crm_owner.html', {
        'daily_revenue': daily_revenue,
        'daily_count': daily_count,
        'bestsellers': bestsellers,
        'busiest_hours': busiest_hours,
        'customers': Customer.objects.order_by('-total_spent')[:100],
        'menu_items': MenuItem.objects.select_related('category').all(),
        'categories': Category.objects.all(),
        'coupons': Coupon.objects.all(),
    })

@shop_device_and_login_required(owner_only=True)
@require_POST
def api_toggle_menu_item(request, item_id):
    item = get_object_or_404(MenuItem, id=item_id)
    item.is_available = not item.is_available
    item.save()
    return JsonResponse({'success': True, 'is_available': item.is_available})



@shop_device_and_login_required(owner_only=True)
@require_POST
def api_save_menu_item(request):
    data = json.loads(request.body)
    item_id = data.get('id')
    if item_id:
        item = get_object_or_404(MenuItem, id=item_id)
        item.price = Decimal(str(data.get('price', item.price)))
        item.name = data.get('name', item.name)
        item.save()
    else:
        cat_name = data.get('category', 'Signatures').strip()
        category, _ = Category.objects.get_or_create(name=cat_name)
        MenuItem.objects.create(
            category=category,
            name=data['name'].strip(),
            description=data.get('description', '').strip(),
            price=Decimal(str(data['price'])),
            image_url=data.get('image_url', '').strip(),
            is_bestseller=bool(data.get('is_bestseller', False))
        )
    return JsonResponse({'success': True})

@shop_device_and_login_required(owner_only=True)
@require_POST
def api_create_coupon(request):
    data = json.loads(request.body)
    Coupon.objects.update_or_create(
        code=data['code'].strip().upper(),
        defaults={
            'discount_type': data.get('discount_type', 'FLAT'),
            'value': Decimal(str(data['value'])),
            'min_order_amount': Decimal(str(data.get('min_order_amount', 0))),
            'max_discount': Decimal(str(data.get('max_discount', 100))),
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
    coupon.save()
    return JsonResponse({'success': True, 'is_active': coupon.is_active})

def crm_logout(request):
    logout(request)
    return redirect('crm_login')


@require_POST
def api_customer_auth(request):
    data = json.loads(request.body)
    phone = data.get('phone', '').strip()
    pin = data.get('pin', '').strip()
    name = data.get('name', '').strip()
    action = data.get('action')

    if not phone or not pin:
        return JsonResponse({'success': False, 'error': 'Phone and PIN are required.'})

    if action == 'signup':
        if User.objects.filter(username=phone).exists():
            return JsonResponse({'success': False, 'error': 'Number already registered. Please login.'})
        
        # Create User & link to existing guest Customer Profile (or create new one)
        with transaction.atomic():
            user = User.objects.create_user(username=phone, password=pin)
            
            # Check if this phone number already exists from a previous guest order
            customer = Customer.objects.filter(phone=phone).first()
            if customer:
                # Link the new secure user account to their old order history!
                customer.user = user
                if name:
                    customer.name = name  # Update to their newly provided name
                customer.save()
                login(request, user)
                return JsonResponse({'success': True, 'name': customer.name})
            else:
                # Completely new customer
                Customer.objects.create(user=user, phone=phone, name=name)
                login(request, user)
                return JsonResponse({'success': True, 'name': name})

    elif action == 'login':
        user = authenticate(request, username=phone, password=pin)
        if user is not None:
            login(request, user)
            return JsonResponse({'success': True, 'name': user.customer_profile.name})
        return JsonResponse({'success': False, 'error': 'Invalid Phone Number or PIN.'})

    

# 4. Customer Order History Dashboard
def customer_dashboard(request):
    if not request.user.is_authenticated or request.user.is_staff:
        return redirect('public_menu')
    
    orders = Order.objects.filter(customer=request.user.customer_profile).order_by('-created_at')
    return render(request, 'customer_dashboard.html', {'orders': orders})