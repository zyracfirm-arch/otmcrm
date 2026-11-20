"""
URL configuration for otm_project project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/6.1/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""

from django.urls import path
from core import views

urlpatterns = [
    # Public Storefront & Live Order Tracker
    path('', views.public_menu, name='public_menu'),
    path('about/', views.about_page, name='about_page'),
    
    path('api/validate-coupon/', views.api_validate_coupon, name='validate_coupon'),
    path('api/place-order/', views.api_place_order, name='place_order'),
    path('track/<uuid:token>/', views.track_order, name='track_order'),
    path('otm-vault-9921/shop-status/', views.api_toggle_shop_status, name='api_toggle_shop_status'),

    # One-Time Shop Hardware Lock Activation
    path('otm-device-authorize-9921/', views.activate_shop_device, name='activate_device'),

    # Shop CRM Vault (Protected by Hardware Cookie + Staff Auth)
    path('otm-vault-9921/login/', views.crm_login, name='crm_login'),
    path('otm-vault-9921/logout/', views.crm_logout, name='crm_logout'),
    path('otm-vault-9921/inbox/', views.crm_orders_inbox, name='crm_orders_inbox'),
    path('otm-vault-9921/order/<int:order_id>/status/', views.api_update_order_status, name='update_order_status'),
    path('otm-vault-9921/owner/', views.crm_owner_portal, name='crm_owner_portal'),
    path('otm-vault-9921/menu/<int:item_id>/toggle/', views.api_toggle_menu_item, name='toggle_menu_item'),
    path('otm-vault-9921/menu/save/', views.api_save_menu_item, name='save_menu_item'),
    path('otm-vault-9921/coupon/create/', views.api_create_coupon, name='create_coupon'),
    path('otm-vault-9921/coupon/<int:coupon_id>/toggle/', views.api_toggle_coupon, name='toggle_coupon'),
    path('otm-vault-9921/order/<int:order_id>/assign/', views.api_assign_delivery, name='assign_delivery'),
    path('otm-vault-9921/rider/', views.delivery_dashboard, name='delivery_dashboard'),

    path('api/auth/', views.api_customer_auth, name='customer_auth'),
    path('my-orders/', views.customer_dashboard, name='customer_dashboard'),


    path('otm-vault-9921/api/kds-poll/', views.api_kds_poll, name='api_kds_poll'),

    path('otm-vault-9921/staff/create/', views.api_create_staff, name='api_create_staff'),
    path('otm-vault-9921/staff/<int:user_id>/delete/', views.api_delete_staff, name='api_delete_staff'),

    path('account/', views.customer_auth_page, name='customer_auth_page'),
    path('api/customer/change-password/', views.api_customer_change_password, name='api_customer_change_password'),

    path('otm-vault-9921/api/note/send/', views.api_owner_send_note, name='api_owner_send_note'),
    path('otm-vault-9921/api/note/ack/', views.api_ack_staff_note, name='api_ack_staff_note'),

]