select
    p.order_id,
    sum(p.amount * fx.usd_rate) as paid_usd,
    max(p.paid_at) as last_paid_at,
    count(*) as payment_count
from {{ ref('stg_payments') }} as p
join {{ ref('stg_orders') }} as o
    on o.order_id = p.order_id
join {{ ref('stg_fx_rates') }} as fx
    on fx.currency = o.currency
    and fx.rate_date = cast(p.paid_at as date)
where p.status = 'settled'
group by p.order_id
