select
    payment_id,
    order_id,
    method,
    amount,
    lower(status) as status,
    paid_at
from {{ source('raw', 'payments') }}
