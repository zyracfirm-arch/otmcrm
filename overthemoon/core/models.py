import uuid
from django.db import models
from django.utils import timezone
from django.contrib.auth.models import User
import random




class Category(models.Model):
    name = models.CharField(max_length=80)
    display_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['display_order', 'name']

    def __str__(self):
        return self.name

class MenuItem(models.Model):
    category = models.ForeignKey(Category, on_delete=models.CASCADE, related_name='items')
    name = models.CharField(max_length=120)
    description = models.CharField(max_length=255, blank=True)
    price = models.DecimalField(max_digits=8, decimal_places=2)
    image_url = models.URLField(blank=True, help_text="Paste image link or static path")
    is_available = models.BooleanField(default=True) # Instant Out-of-Stock toggle
    is_bestseller = models.BooleanField(default=False)

    def __str__(self):
        return f"{self.name} (₹{self.price})"

class Customer(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, null=True, blank=True, related_name='customer_profile')
    phone = models.CharField(max_length=15, unique=True, db_index=True)
    name = models.CharField(max_length=120)
    address = models.TextField(blank=True)
    total_orders = models.PositiveIntegerField(default=0)
    total_spent = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    
    def __str__(self):
        return f"{self.name} ({self.phone})"

class Coupon(models.Model):
    DISCOUNT_TYPES = (('FLAT', 'Flat ₹ Off'), ('PERCENT', 'Percentage % Off'))
    code = models.CharField(max_length=20, unique=True)
    discount_type = models.CharField(max_length=10, choices=DISCOUNT_TYPES, default='FLAT')
    value = models.DecimalField(max_digits=6, decimal_places=2)
    min_order_amount = models.DecimalField(max_digits=8, decimal_places=2, default=0)
    max_discount = models.DecimalField(max_digits=8, decimal_places=2, default=100)
    first_order_only = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    used_count = models.PositiveIntegerField(default=0)

    def __str__(self):
        return self.code

class Order(models.Model):
    STATUS_CHOICES = (
        ('NEW', 'New'),
        ('PREPARING', 'Preparing'),
        ('OUT', 'Out for Delivery / Ready'),
        ('DONE', 'Done'),
        ('CANCELLED', 'Cancelled'),
    )
    ORDER_TYPES = (('DELIVERY', 'Delivery'), ('PICKUP', 'Pickup'))

    order_number = models.CharField(max_length=20, unique=True, editable=False)
    tracking_token = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    customer = models.ForeignKey(Customer, on_delete=models.SET_NULL, null=True, related_name='orders')
    customer_name = models.CharField(max_length=120)
    customer_phone = models.CharField(max_length=15)
    delivery_address = models.TextField(blank=True)
    order_type = models.CharField(max_length=10, choices=ORDER_TYPES, default='DELIVERY')
    status = models.CharField(max_length=15, choices=STATUS_CHOICES, default='NEW', db_index=True)
    delivery_boy = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='deliveries')

    subtotal = models.DecimalField(max_digits=8, decimal_places=2)
    coupon_code = models.CharField(max_length=20, blank=True)
    discount_amount = models.DecimalField(max_digits=8, decimal_places=2, default=0)
    total_amount = models.DecimalField(max_digits=8, decimal_places=2)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    def save(self, *args, **kwargs):
        if not self.order_number:
            # Format: OTM-YYMMDD-HHMMSS-RANDOM (e.g., OTM-261116-140532-842)
            # This makes it mathematically impossible for exact-millisecond orders to merge.
            timestamp = timezone.now().strftime("%y%m%d-%H%M%S")
            rand_suffix = random.randint(100, 999)
            self.order_number = f"OTM-{timestamp}-{rand_suffix}"
        super().save(*args, **kwargs)

class OrderItem(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='items')
    item_name = models.CharField(max_length=120)
    unit_price = models.DecimalField(max_digits=8, decimal_places=2)
    quantity = models.PositiveIntegerField(default=1)