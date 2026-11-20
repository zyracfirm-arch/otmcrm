import uuid
import random
from decimal import Decimal
from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone


class Category(models.Model):
    name = models.CharField(max_length=100, unique=True)
    display_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['display_order', 'id']

    def __str__(self):
        return self.name


class MenuItem(models.Model):
    category = models.ForeignKey(Category, on_delete=models.CASCADE, related_name='items')
    name = models.CharField(max_length=120)
    description = models.TextField(blank=True)
    price = models.DecimalField(max_digits=8, decimal_places=2)
    image_url = models.URLField(blank=True)
    is_veg = models.BooleanField(default=True)
    is_available = models.BooleanField(default=True)
    is_bestseller = models.BooleanField(default=False)

    class Meta:
        ordering = ['-is_bestseller', 'name']

    def __str__(self):
        return f"{self.name} (₹{self.price})"


class Customer(models.Model):
    user = models.OneToOneField(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='customer_profile'
    )
    phone = models.CharField(max_length=15, unique=True, db_index=True)
    name = models.CharField(max_length=120)
    address = models.TextField(blank=True)
    total_orders = models.PositiveIntegerField(default=0)
    total_spent = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    last_order_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)
    auth_hash = models.CharField(max_length=128, blank=True, default='')       # Hashed password or 6-digit OTP
    otp_expires_at = models.DateTimeField(null=True, blank=True)               # 5-minute expiry for OTP
    failed_attempts = models.PositiveSmallIntegerField(default=0)              # Brute-force counter
    locked_until = models.DateTimeField(null=True, blank=True)                 # 15-min lockout timestamp

    def __str__(self):
        return f"{self.name} ({self.phone})"


class Coupon(models.Model):
    DISCOUNT_TYPES = (
        ('FLAT', 'Flat ₹ Off'),
        ('PERCENT', 'Percentage % Off'),
    )
    code = models.CharField(max_length=30, unique=True)
    discount_type = models.CharField(max_length=10, choices=DISCOUNT_TYPES, default='FLAT')
    value = models.DecimalField(max_digits=8, decimal_places=2)
    min_order_amount = models.DecimalField(max_digits=8, decimal_places=2, default=Decimal('0.00'))
    max_discount = models.DecimalField(max_digits=8, decimal_places=2, default=Decimal('100.00'))
    first_order_only = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    used_count = models.PositiveIntegerField(default=0)

    def __str__(self):
        return self.code


class Order(models.Model):
    ORDER_TYPES = (
        ('DELIVERY', 'Delivery'),
        ('PICKUP', 'Pickup'),
    )
    STATUS_CHOICES = (
        ('NEW', 'Order Received'),
        ('PREP', 'Preparing in Kitchen'),
        ('OUT', 'Out for Delivery'),
        ('DONE', 'Delivered'),
        ('CANCELLED', 'Cancelled'),
    )
    PAYMENT_MODE_CHOICES = (
        ('UNPAID', 'Pending'),
        ('CASH', 'Cash'),
        ('UPI', 'UPI / Online'),
    )

    order_number = models.CharField(max_length=30, unique=True, editable=False)
    tracking_token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    customer = models.ForeignKey(Customer, on_delete=models.SET_NULL, null=True, related_name='orders')
    customer_name = models.CharField(max_length=120)
    customer_phone = models.CharField(max_length=15, db_index=True)
    delivery_address = models.TextField(blank=True)
    order_type = models.CharField(max_length=10, choices=ORDER_TYPES, default='DELIVERY')
    status = models.CharField(max_length=15, choices=STATUS_CHOICES, default='NEW', db_index=True)
    payment_mode = models.CharField(max_length=10, choices=PAYMENT_MODE_CHOICES, default='UNPAID', db_index=True)

    delivery_boy = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='assigned_deliveries'
    )

    subtotal = models.DecimalField(max_digits=10, decimal_places=2)
    coupon_code = models.CharField(max_length=30, blank=True)
    discount_amount = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    total_amount = models.DecimalField(max_digits=10, decimal_places=2)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def save(self, *args, **kwargs):
        if not self.order_number:
            now_str = timezone.localtime().strftime('%y%m%d-%H%M%S')
            rand_suffix = random.randint(100, 999)
            self.order_number = f"OTM-{now_str}-{rand_suffix}"
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.order_number} - {self.customer_name}"


class OrderItem(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='items')
    item_name = models.CharField(max_length=150)
    unit_price = models.DecimalField(max_digits=8, decimal_places=2)
    quantity = models.PositiveIntegerField(default=1)
    is_veg = models.BooleanField(default=True)

    @property
    def subtotal(self):
        return self.unit_price * self.quantity

    def __str__(self):
        return f"{self.quantity} x {self.item_name}"


class ShopSettings(models.Model):
    is_open = models.BooleanField(default=True)
    reopen_time = models.CharField(max_length=50, default="7:00 PM")
    closed_reason = models.CharField(
        max_length=120,
        blank=True,
        default="Kitchen is resting"
    )
    updated_at = models.DateTimeField(auto_now=True)

    @classmethod
    def get_solo(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    def __str__(self):
        return f"Shop Status: {'OPEN' if self.is_open else 'CLOSED'} (Opens at {self.reopen_time})"