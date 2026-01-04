# Store Service

E-commerce module for SwimBuddz - allows members and visitors to purchase swimming gear.

## Features
- Product catalog with categories and collections
- Product variants (size, color)
- Inventory tracking with reservations
- Shopping cart with member discounts
- Checkout with Paystack integration
- Order management
- Pickup locations (dynamic from database)
- Home delivery option
- Store credits for refunds

## Port
8010

## Dependencies
- `payments_service` for Paystack integration
- `members_service` for member tier lookup
- `communications_service` for order notifications
- Redis for event bus
