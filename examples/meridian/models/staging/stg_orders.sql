select
    order_id,
    customer_id,
    lower(status) as status,
    ordered_at,
    cast(ordered_at as date) as order_date,
    upper(currency) as currency,
    coalesce(shipping_amount, 0) as shipping_amount,
    coalesce(discount_amount, 0) as discount_amount
from {{ source('raw', 'orders') }}
where not is_test
