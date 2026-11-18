import json
from decimal import Decimal

from django.conf import settings
from django.core import signing
from django.http import HttpResponse
from django.contrib.auth.models import User
from django.test import TestCase, Client
from django.urls import reverse

from .models import Category, MenuItem, Customer, Coupon, Order, OrderItem


class OverTheMoonFullSystemTests(TestCase):
    def setUp(self):
        self.client = Client()

        # 1. Setup Staff Accounts (Owner, Kitchen, Rider)
        self.owner = User.objects.create_superuser(
            username='ubedullah',
            email='owner@otm.in',
            password='password123'
        )
        self.kitchen = User.objects.create_user(
            username='kitchen',
            password='password123',
            is_staff=True
        )
        self.rider = User.objects.create_user(
            username='rider1',
            password='password123',
            is_staff=True
        )

        # 2. Setup Categories & Menu Items (Pure Veg + Non-Veg + Sold Out)
        self.cat_burgers = Category.objects.create(name='Loaded Burgers', display_order=1)
        self.cat_sides = Category.objects.create(name='Crispy Sides', display_order=2)

        self.veg_item = MenuItem.objects.create(
            category=self.cat_burgers,
            name='Paneer Cheese Burst Burger',
            description='Double paneer patty with molten cheddar',
            price=Decimal('149.00'),
            is_veg=True,
            is_available=True,
            is_bestseller=True,
        )
        self.nonveg_item = MenuItem.objects.create(
            category=self.cat_burgers,
            name='Crispy Zinger Chicken Burger',
            description='Crunchy fried chicken fillet with secret OTM dip',
            price=Decimal('139.00'),
            is_veg=False,
            is_available=True,
            is_bestseller=True,
        )
        self.sold_out_item = MenuItem.objects.create(
            category=self.cat_sides,
            name='Peri Peri Fries',
            price=Decimal('99.00'),
            is_veg=True,
            is_available=False,
        )

        # 3. Setup Active Coupons (Flat & First-Order Only)
        self.flat_coupon = Coupon.objects.create(
            code='OTM50',
            discount_type='FLAT',
            value=Decimal('50.00'),
            min_order_amount=Decimal('250.00'),
            max_discount=Decimal('50.00'),
            first_order_only=False,
            is_active=True,
        )
        self.first_coupon = Coupon.objects.create(
            code='FIRST100',
            discount_type='FLAT',
            value=Decimal('100.00'),
            min_order_amount=Decimal('200.00'),
            first_order_only=True,
            is_active=True,
        )

        # 4. Setup Customer Profiles Across All 3 Tiers (New, Normal, OTM Premium)
        self.new_user = User.objects.create_user(username='9876543210', password='1234', first_name='Aarav')
        self.new_customer = Customer.objects.create(
            user=self.new_user,
            phone='9876543210',
            name='Aarav',
            address='Khamasa Cross Road, Ahmedabad',
            total_orders=0,
            total_spent=Decimal('0.00'),
        )

        self.normal_user = User.objects.create_user(username='9876543211', password='1234', first_name='Zoya')
        self.normal_customer = Customer.objects.create(
            user=self.normal_user,
            phone='9876543211',
            name='Zoya',
            total_orders=3,
            total_spent=Decimal('1200.00'),
        )

        # Create a past delivered order for Zoya so her dashboard renders an order card + Reorder button
        zoya_order = Order.objects.create(
            customer=self.normal_customer,
            customer_name=self.normal_customer.name,
            customer_phone=self.normal_customer.phone,
            delivery_address='Khamasa, Ahmedabad',
            order_type='DELIVERY',
            subtotal=Decimal('149.00'),
            total_amount=Decimal('149.00'),
            status='DONE',
        )
        OrderItem.objects.create(
            order=zoya_order,
            item_name=self.veg_item.name,
            unit_price=Decimal('149.00'),
            quantity=1,
            is_veg=True,
        )

        self.premium_user = User.objects.create_user(username='9876543212', password='1234', first_name='Faiz')
        self.premium_customer = Customer.objects.create(
            user=self.premium_user,
            phone='9876543212',
            name='Faiz',
            address='Old City, Ahmedabad',
            total_orders=14,
            total_spent=Decimal('6250.00'),
        )

    def _authorize_shop_device(self):
        """Helper to generate the exact signed hardware cookie Django expects."""
        dummy_response = HttpResponse()
        dummy_response.set_signed_cookie(
            'otm_shop_device', 
            settings.SHOP_DEVICE_SECRET, 
            salt='zyrac_lock'
        )
        
        # Inject the perfectly signed cookie into the test browser
        self.client.cookies['otm_shop_device'] = dummy_response.cookies['otm_shop_device'].value

    # ==========================================================
    # 1. PUBLIC STOREFRONT, DIETARY BADGES & COUPON TESTS
    # ==========================================================
    def test_public_menu_renders_veg_and_nonveg_items(self):
        res = self.client.get('/')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.context['veg_count'], 1)
        self.assertEqual(res.context['nonveg_count'], 1)
        self.assertContains(res, 'Paneer Cheese Burst Burger')
        self.assertContains(res, 'Crispy Zinger Chicken Burger')

    def test_coupon_validation_rules(self):
        res = self.client.post(
            '/api/validate-coupon/',
            data=json.dumps({'code': 'OTM50', 'subtotal': 300, 'phone': '9876543210'}),
            content_type='application/json',
        )
        data = res.json()
        self.assertTrue(data['valid'])
        self.assertEqual(data['discount'], 50.0)
        self.assertEqual(data['final_total'], 250.0)

        # First-order-only coupon rejected for repeat customer
        res_repeat = self.client.post(
            '/api/validate-coupon/',
            data=json.dumps({'code': 'FIRST100', 'subtotal': 300, 'phone': '9876543211'}),
            content_type='application/json',
        )
        self.assertFalse(res_repeat.json()['valid'])

    # ==========================================================
    # 2. CUSTOMER AUTH & MIXED VEG + NON-VEG ORDER PLACEMENT
    # ==========================================================
    def test_customer_signup_and_login_flow(self):
        res = self.client.post(
            '/api/auth/',
            data=json.dumps({'action': 'signup', 'phone': '9111122222', 'pin': '4321', 'name': 'Kabir'}),
            content_type='application/json',
        )
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.json()['success'])
        self.assertTrue(Customer.objects.filter(phone='9111122222', name='Kabir').exists())

    def test_place_mixed_veg_nonveg_order_and_track_page(self):
        # Unauthenticated checkout guard
        unauth_res = self.client.post(
            '/api/place-order/',
            data=json.dumps({'cart': [{'id': self.veg_item.id, 'qty': 1}], 'order_type': 'PICKUP'}),
            content_type='application/json',
        )
        self.assertIn(unauth_res.status_code, (200, 401))
        self.assertFalse(unauth_res.json()['success'])

        # Login as customer and place a Mixed Veg + Non-Veg order with coupon
        self.client.force_login(self.new_user)
        payload = {
            'cart': [
                {'id': self.veg_item.id, 'qty': 1},      # 149 (Veg)
                {'id': self.nonveg_item.id, 'qty': 2},   # 278 (Non-Veg) -> Subtotal = 427
            ],
            'order_type': 'DELIVERY',
            'address': '14 Khambhati Manzil, Dhalgarwad, Ahmedabad',
            'coupon_code': 'OTM50',
        }
        res = self.client.post(
            '/api/place-order/',
            data=json.dumps(payload),
            content_type='application/json',
        )
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data['success'])

        order = Order.objects.get(order_number=data['order_number'])
        self.assertEqual(order.subtotal, Decimal('427.00'))
        self.assertEqual(order.discount_amount, Decimal('50.00'))
        self.assertEqual(order.total_amount, Decimal('377.00'))
        self.assertEqual(order.items.count(), 2)

        veg_line = order.items.get(item_name=self.veg_item.name)
        nonveg_line = order.items.get(item_name=self.nonveg_item.name)
        self.assertTrue(veg_line.is_veg)
        self.assertFalse(nonveg_line.is_veg)

        # Customer profile stats updated
        self.new_customer.refresh_from_db()
        self.assertEqual(self.new_customer.total_orders, 1)
        self.assertEqual(self.new_customer.total_spent, Decimal('377.00'))

        # Verify live tracker page renders astronaut & mixed hygiene note
        track_res = self.client.get(data['tracking_url'])
        self.assertEqual(track_res.status_code, 200)
        self.assertContains(track_res, 'astronautCarrier')
        self.assertContains(track_res, 'mixedPackagingNote')

    def test_sold_out_item_blocked_at_checkout(self):
        self.client.force_login(self.new_user)
        res = self.client.post(
            '/api/place-order/',
            data=json.dumps({
                'cart': [{'id': self.sold_out_item.id, 'name': self.sold_out_item.name, 'qty': 1}],
                'order_type': 'PICKUP',
            }),
            content_type='application/json',
        )
        self.assertEqual(res.status_code, 400)
        self.assertFalse(res.json()['success'])

    # ==========================================================
    # 3. CUSTOMER DASHBOARD & OTM PREMIUM TIER (₹5,000+) TESTS
    # ==========================================================
    def test_customer_dashboard_premium_badge_and_no_guilt_spend(self):
        # Normal member (< ₹5,000) does NOT see OTM Premium badge, DOES see 1-Tap Reorder
        self.client.force_login(self.normal_user)
        res_normal = self.client.get(reverse('customer_dashboard'))
        self.assertEqual(res_normal.status_code, 200)
        self.assertNotContains(res_normal, 'OTM Astronaut Premium')
        self.assertContains(res_normal, 'reorderMeal(this)')

        # Premium member (>= ₹5,000) unlocks OTM Premium badge without displaying raw ₹ spent
        self.client.force_login(self.premium_user)
        res_prem = self.client.get(reverse('customer_dashboard'))
        self.assertEqual(res_prem.status_code, 200)
        self.assertContains(res_prem, 'OTM Astronaut Premium')
        self.assertNotContains(res_prem, '6250')

    # ==========================================================
    # 4. KITCHEN KDS, RIDER DISPATCH & DELIVERY COMPLETION FLOW
    # ==========================================================
    def test_kitchen_to_rider_to_delivered_lifecycle(self):
        order = Order.objects.create(
            customer=self.normal_customer,
            customer_name=self.normal_customer.name,
            customer_phone=self.normal_customer.phone,
            delivery_address='Kalupur, Ahmedabad',
            order_type='DELIVERY',
            subtotal=Decimal('288.00'),
            total_amount=Decimal('288.00'),
            status='NEW',
        )
        OrderItem.objects.create(order=order, item_name=self.veg_item.name, unit_price=Decimal('149.00'), quantity=1, is_veg=True)
        OrderItem.objects.create(order=order, item_name=self.nonveg_item.name, unit_price=Decimal('139.00'), quantity=1, is_veg=False)

        # Unactivated device cannot access CRM login
        self.assertEqual(self.client.get(reverse('crm_login')).status_code, 404)

        # Activate shop device and log in as Kitchen
        self._authorize_shop_device()
        self.client.force_login(self.kitchen)

        inbox_res = self.client.get(reverse('crm_orders_inbox'))
        self.assertEqual(inbox_res.status_code, 200)
        self.assertContains(inbox_res, order.order_number)
        self.assertContains(inbox_res, 'MIXED ORDER')

        # Kitchen moves order to PREP
        self.client.post(
            f'/otm-vault-9921/order/{order.id}/status/',
            data=json.dumps({'status': 'PREP'}),
            content_type='application/json',
        )
        order.refresh_from_db()
        self.assertEqual(order.status, 'PREP')

        # Kitchen assigns Rider1 -> moves to OUT
        self.client.post(
            f'/otm-vault-9921/order/{order.id}/assign/',
            data=json.dumps({'rider_id': self.rider.id}),
            content_type='application/json',
        )
        order.refresh_from_db()
        self.assertEqual(order.status, 'OUT')
        self.assertEqual(order.delivery_boy, self.rider)

        # Rider checks Delivery Dashboard & completes order
        self.client.force_login(self.rider)
        rider_res = self.client.get(reverse('delivery_dashboard'))
        self.assertEqual(rider_res.status_code, 200)
        self.assertContains(rider_res, order.order_number)
        self.assertContains(rider_res, 'MIXED BAG')

        self.client.post(
            f'/otm-vault-9921/order/{order.id}/status/',
            data=json.dumps({'status': 'DONE'}),
            content_type='application/json',
        )
        order.refresh_from_db()
        self.assertEqual(order.status, 'DONE')

        # Customer tracker now shows "Enjoy Your Meal" instead of "on the way"
        track_done_res = self.client.get(reverse('track_order', args=[order.tracking_token]))
        self.assertContains(track_done_res, 'Enjoy Your Meal!')
        self.assertNotContains(track_done_res, 'Rider1 is on the way')

    # ==========================================================
    # 5. OWNER VAULT ANALYTICS, TIERS, MENU & COUPON CONTROLS
    # ==========================================================
    def test_owner_portal_tiers_menu_and_coupons(self):
        self._authorize_shop_device()

        # Non-owner staff (kitchen) is blocked from Owner Vault
        self.client.force_login(self.kitchen)
        blocked_res = self.client.get(reverse('crm_owner_portal'))
        self.assertNotEqual(blocked_res.status_code, 200)

        # Owner accesses Owner Vault
        self.client.force_login(self.owner)
        owner_res = self.client.get(reverse('crm_owner_portal'))
        self.assertEqual(owner_res.status_code, 200)
        self.assertEqual(owner_res.context['premium_count'], 1)
        self.assertEqual(owner_res.context['normal_count'], 1)
        self.assertEqual(owner_res.context['new_member_count'], 1)
        self.assertContains(owner_res, 'OTM Premium')
        self.assertContains(owner_res, 'Normal Member')
        self.assertContains(owner_res, 'New Member')

        # Owner adds a new Non-Veg item
        save_res = self.client.post(
            '/otm-vault-9921/menu/save/',
            data=json.dumps({
                'category': 'Loaded Burgers',
                'name': 'Double Mutton Smash Burger',
                'price': '229',
                'is_veg': False,
                'is_bestseller': True,
            }),
            content_type='application/json',
        )
        self.assertTrue(save_res.json()['success'])
        created_dish = MenuItem.objects.get(name='Double Mutton Smash Burger')
        self.assertFalse(created_dish.is_veg)

        # Owner switches dish diet to Pure Veg
        self.client.post(
            '/otm-vault-9921/menu/save/',
            data=json.dumps({
                'id': created_dish.id,
                'name': created_dish.name,
                'price': '229',
                'is_veg': True,
            }),
            content_type='application/json',
        )
        created_dish.refresh_from_db()
        self.assertTrue(created_dish.is_veg)

        # Owner toggles stock off & creates a new promo coupon
        self.client.post(f'/otm-vault-9921/menu/{created_dish.id}/toggle/')
        created_dish.refresh_from_db()
        self.assertFalse(created_dish.is_available)

        cp_res = self.client.post(
            '/otm-vault-9921/coupon/create/',
            data=json.dumps({
                'code': 'MOON20',
                'discount_type': 'PERCENT',
                'value': '20',
                'min_order_amount': '300',
                'max_discount': '120',
            }),
            content_type='application/json',
        )
        self.assertTrue(cp_res.json()['success'])
        self.assertTrue(Coupon.objects.filter(code='MOON20', is_active=True).exists())