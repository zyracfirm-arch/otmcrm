from django.test import TestCase

# Create your tests here.
import json
from decimal import Decimal
from django.test import TestCase, Client
from django.conf import settings
from django.contrib.auth.models import User
from core.models import Category, MenuItem, Customer, Coupon, Order, OrderItem


class OverTheMoonFullSystemTests(TestCase):
    def setUp(self):
        self.client = Client()

        # 1. Create Staff Accounts (Owner, Kitchen, Rider)
        self.owner = User.objects.create_superuser(
            username='ubedullah', password='otmowner2026', email='owner@otm.in'
        )
        self.kitchen = User.objects.create_user(
            username='kitchen', password='otmkitchen2026', is_staff=True
        )
        self.rider = User.objects.create_user(
            username='rider1', password='rider123', is_staff=True
        )

        # 2. Seed Menu Category & Burger
        self.cat = Category.objects.create(name='Veg Burger')
        self.burger = MenuItem.objects.create(
            category=self.cat,
            name='Paneer Cheese Burst',
            description='Deep Crispy Paneer Piece & Cheese Patty with Sauces',
            price=Decimal('170.00'),
            is_available=True,
            is_bestseller=True
        )

        # 3. Seed Promo Coupon
        self.coupon = Coupon.objects.create(
            code='OTM50',
            discount_type='FLAT',
            value=Decimal('50.00'),
            min_order_amount=Decimal('200.00'),
            max_discount=Decimal('50.00'),
            first_order_only=False,
            is_active=True
        )

    def authorize_device_cookie(self):
        """Helper to unlock the hardware device cookie on self.client."""
        res = self.client.post('/otm-device-authorize-9921/', {
            'secret_pin': settings.SHOP_DEVICE_SECRET
        })
        self.assertEqual(res.status_code, 302)

    def test_1_public_menu_loads(self):
        res = self.client.get('/')
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, 'Paneer Cheese Burst')
        self.assertContains(res, 'OTM50')

    def test_2_unauthenticated_checkout_returns_401(self):
        res = self.client.post(
            '/api/place-order/',
            data=json.dumps({'cart': [{'id': self.burger.id, 'qty': 2}], 'order_type': 'DELIVERY', 'address': 'Khamasa'}),
            content_type='application/json'
        )
        self.assertEqual(res.status_code, 401)

    def test_3_customer_signup_login_and_guest_history_link(self):
        # Create a pre-existing guest customer record
        Customer.objects.create(phone='9876543210', name='Old Guest', total_orders=2, total_spent=Decimal('340.00'))

        # Sign up with the same phone number
        res = self.client.post(
            '/api/auth/',
            data=json.dumps({'action': 'signup', 'phone': '9876543210', 'pin': '1234', 'name': 'Faiz Mansuri'}),
            content_type='application/json'
        )
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data['success'])
        self.assertEqual(data['name'], 'Faiz Mansuri')

        # Verify old order count was preserved
        cust = Customer.objects.get(phone='9876543210')
        self.assertEqual(cust.total_orders, 2)
        self.assertIsNotNone(cust.user)

    def test_4_coupon_validation_and_atomic_order_placement(self):
        # Sign up customer
        self.client.post(
            '/api/auth/',
            data=json.dumps({'action': 'signup', 'phone': '9904123420', 'pin': '4321', 'name': 'Test Buyer'}),
            content_type='application/json'
        )

        # Validate coupon (2 burgers = 340 >= 200 min)
        cp_res = self.client.post(
            '/api/validate-coupon/',
            data=json.dumps({'code': 'OTM50', 'subtotal': 340, 'phone': '9904123420'}),
            content_type='application/json'
        )
        self.assertTrue(cp_res.json()['valid'])
        self.assertEqual(cp_res.json()['discount'], 50.0)

        # Place order (even if frontend sends fake price, backend uses DB price 170 * 2 - 50 = 290)
        order_res = self.client.post(
            '/api/place-order/',
            data=json.dumps({
                'cart': [{'id': self.burger.id, 'qty': 2, 'price': 1}],
                'address': 'Near Mansuri Masjid, Khamasa',
                'order_type': 'DELIVERY',
                'coupon_code': 'OTM50'
            }),
            content_type='application/json'
        )
        self.assertEqual(order_res.status_code, 200)
        order_data = order_res.json()
        self.assertTrue(order_data['success'])

        order = Order.objects.get(order_number=order_data['order_number'])
        self.assertEqual(order.total_amount, Decimal('290.00'))

        # Test Live Order Tracker HTML & AJAX Polling
        track_res = self.client.get(order_data['tracking_url'])
        self.assertEqual(track_res.status_code, 200)

        ajax_res = self.client.get(order_data['tracking_url'], HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(ajax_res.json()['status'], 'NEW')

    def test_5_hardware_lock_blocks_unauthorized_devices(self):
        # Without cookie, /otm-vault-9921/login/ must return 404
        res = self.client.get('/otm-vault-9921/login/')
        self.assertEqual(res.status_code, 404)

        # Authorize device cookie
        self.authorize_device_cookie()
        res_after = self.client.get('/otm-vault-9921/login/')
        self.assertEqual(res_after.status_code, 200)

    def test_6_staff_role_routing_and_rider_dispatch(self):
        self.authorize_device_cookie()

        # Create a test order
        cust = Customer.objects.create(phone='9111111111', name='Buyer')
        order = Order.objects.create(
            customer=cust, customer_name='Buyer', customer_phone='9111111111',
            delivery_address='Kalupur', order_type='DELIVERY',
            subtotal=Decimal('170.00'), total_amount=Decimal('170.00')
        )

        # Kitchen login -> Redirects to crm_orders_inbox
        login_kitchen = self.client.post('/otm-vault-9921/login/', {'username': 'kitchen', 'password': 'otmkitchen2026'})
        self.assertRedirects(login_kitchen, '/otm-vault-9921/inbox/')

        # Kitchen moves order to PREP
        self.client.post(
            f'/otm-vault-9921/order/{order.id}/status/',
            data=json.dumps({'status': 'PREP'}),
            content_type='application/json'
        )
        order.refresh_from_db()
        self.assertEqual(order.status, 'PREP')

        # Kitchen assigns order to rider1 -> Moves to OUT
        self.client.post(
            f'/otm-vault-9921/order/{order.id}/assign/',
            data=json.dumps({'rider_id': self.rider.id}),
            content_type='application/json'
        )
        order.refresh_from_db()
        self.assertEqual(order.status, 'OUT')
        self.assertEqual(order.delivery_boy, self.rider)

        # Rider login -> Redirects to delivery_dashboard
        self.client.get('/otm-vault-9921/logout/')
        login_rider = self.client.post('/otm-vault-9921/login/', {'username': 'rider1', 'password': 'rider123'})
        self.assertRedirects(login_rider, '/otm-vault-9921/rider/')

        # Rider marks order DONE
        self.client.post(
            f'/otm-vault-9921/order/{order.id}/status/',
            data=json.dumps({'status': 'DONE'}),
            content_type='application/json'
        )
        order.refresh_from_db()
        self.assertEqual(order.status, 'DONE')

    def test_7_owner_portal_permissions_and_crud(self):
        self.authorize_device_cookie()

        # Kitchen cannot open Owner Portal
        self.client.login(username='kitchen', password='otmkitchen2026')
        denied_res = self.client.get('/otm-vault-9921/owner/')
        self.assertNotEqual(denied_res.status_code, 200)

        # Owner can open Owner Portal and add a dish
        self.client.login(username='ubedullah', password='otmowner2026')
        owner_res = self.client.get('/otm-vault-9921/owner/')
        self.assertEqual(owner_res.status_code, 200)

        save_res = self.client.post(
            '/otm-vault-9921/menu/save/',
            data=json.dumps({'category': 'Tasty Veg Roll', 'name': 'Paneer Roll', 'price': '150'}),
            content_type='application/json'
        )
        self.assertTrue(save_res.json()['success'])
        self.assertTrue(MenuItem.objects.filter(name='Paneer Roll').exists())