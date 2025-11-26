# Payments Service

SwimBuddz Payments Service manages membership fees, session payments, and financial transactions.

## Features

- Payment processing integration
- Membership fee management
- Session-based payments
- Payment history tracking
- Invoice generation
- Payment status monitoring

## API Endpoints

### Payments
- `GET /payments` - List payments
- `GET /payments/{id}` - Get payment details
- `POST /payments` - Initiate payment
- `POST /payments/{id}/verify` - Verify payment status
- `GET /payments/member/{member_id}` - Member payment history

### Invoices
- `GET /invoices/{member_id}` - Get member invoices
- `POST /invoices` - Generate invoice
- `GET /invoices/{id}/download` - Download invoice PDF

## Database Tables

- `payments` - Payment transaction records
- `invoices` - Generated invoices
- `payment_plans` - Subscription/installment plans

## Key Features

### Payment Methods
- Paystack integration (primary)
- Flutterwave support (alternative)
- Bank transfer verification
- Cash payment recording

### Membership Tiers
- **Community**: Free tier (no payments)
- **Club**: Monthly/annual subscription
- **Academy**: Program-based payment

### Payment Tracking
- Pending/processing/completed/failed status
- Payment reminders
- Overdue notifications
- Receipt generation

## Environment Variables

See `.env.dev` for required configuration:
- `DATABASE_URL` - PostgreSQL connection string
- `PAYSTACK_SECRET_KEY` - Payment gateway key
- `PAYSTACK_PUBLIC_KEY` - Public API key

## Running

```bash
# Via Docker
docker-compose up payments-service

# Standalone (dev)
cd services/payments_service
uvicorn app.main:app --host 0.0.0.0 --port 8005 --reload
```

## Port

Default: `8005`

## Security Notes

- All payment keys stored in environment variables
- Never commit payment credentials
- Use test keys in development
- Webhook signature verification required
