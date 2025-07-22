# Pavilion Restaurant Management System - AI Coding Agent Instructions

## Project Overview
Pavilion is a Django-based restaurant management system with real-time order processing, multi-station kitchen workflows, POS integration, and device authorization. The system handles in-person, online pickup, and delivery orders with comprehensive payment and gift card support.

## Architecture & Core Components

### Multi-App Django Structure
- **pos_server**: Core order management, dishes, stations, device authorization
- **payments**: Square API integration, payment authorizations, transactions  
- **gift_cards**: Gift card balance management and payment integration
- **inventory**: Stock management, recipes, waste tracking with POS integration
- **deliveries**: Delivery orders with courier assignment and tracking
- **online_store**: Customer-facing e-commerce frontend
- **webrtc**: Real-time communication for kitchen displays
- **users**: Authentication and role-based access control

### Order Workflow & Station System
Orders flow through multiple stations (kitchen, bar, grab-and-go) with independent status tracking:
```python
# Each order tracks status per station (0=pending, 1=approved, 2=completed, 3=rejected, 4=not required)
kitchen_status, bar_status, gng_status = models.PositiveSmallIntegerField(choices=station_statuses)
```

Station assignment is transitioning from string choices to the new `Station` model - check both `station` and `new_station` fields when working with dishes.

### Device Authorization Pattern
Critical security feature - devices must be pre-authorized via `EligibleDevice` model with UUID tokens:
```python
# Always validate device tokens in POS/order-marking endpoints
if not EligibleDevice.objects.filter(token=request_token).exists():
    return HttpResponseForbidden()
```

### Caching Strategy
Active orders are cached for performance (`update_active_orders_cache()` in `pos_server/models.py`). The cache auto-updates on order saves/deletes - manually trigger updates when modifying order status via direct SQL.

## Development Patterns

### Model Relationships
- Orders link to dishes via `OrderDish` through-model for quantity/customization
- Dishes have many-to-many with `Component` via `DishComponent` for recipe building
- Gift cards connect to orders via `GiftCardAuthorization` for payment splitting
- Inventory recipes link to dishes/components for automatic stock deduction

### Payment Integration
Dual payment system supporting both Square payments and gift cards:
```python
# Order.authorization links to PaymentAuthorization for Square
# Order.gift_cards M2M for gift card payments via GiftCardAuthorization
```

### Translation & Internationalization
URLs use `i18n_patterns()` - always wrap URL patterns appropriately. Translation files are in `locale/` with support for Ukrainian and Chinese.

## Key Environment Variables (.env file)
```bash
SQUARE_ACCESS_TOKEN, SQUARE_ENVIRONMENT, SQUARE_DEVICE_ID, SQUARE_LOCATION_ID
SENDGRID_API_KEY, GOOGLE_API_KEY, WEATHER_API_KEY
VAPID_PUBLIC_KEY, VAPID_PRIVATE_KEY  # For push notifications
APP_DOMAIN  # For CORS and allowed hosts
```

## Development Workflow

### Local Development
```bash
# Standard Django commands
python manage.py migrate
python manage.py runserver

# For ngrok development (updates settings.py with new URL)
start.bat  # Windows - starts ngrok and prompts for URL update
```

### Database Patterns
- Uses SQLite for development, PostgreSQL for production (Docker)
- Models extensively use `related_name` for reverse relationships
- Foreign keys typically use `DO_NOTHING` for business data integrity

### Testing & API Patterns
- Views use `@csrf_exempt` for API endpoints receiving JSON
- Device authorization required for POS operations (`device_elig` function)
- Order status updates trigger cache refreshes automatically
- Use `@login_required` for most views, device token validation for POS endpoints

## Common Customization Points

### Adding New Stations
1. Create `Station` model instance with friendly_name, code, icon
2. Update dish assignments to use `new_station` field
3. Add status tracking fields to `Order` model if needed

### Payment Method Integration
Follow the `gift_cards` app pattern - create authorization model linking to orders with amount tracking.

### Inventory Integration
Link new items to `inventory.Recipe` model for automatic stock management when orders are processed.

## Important Files for Context
- `pos_server/models.py`: Core business logic and caching functions
- `pavilion/settings.py`: App configuration and external service keys  
- `pos_server/views.py`: Device auth patterns and order processing APIs
- `payments/square.py`: Square payment integration patterns
- `start.bat`: Development environment setup and ngrok configuration
