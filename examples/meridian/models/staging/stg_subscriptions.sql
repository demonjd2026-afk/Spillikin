select
    subscription_id,
    customer_id,
    plan,
    monthly_price,
    started_at,
    cancelled_at,
    cancelled_at is null as is_active
from {{ source('raw', 'subscriptions') }}
