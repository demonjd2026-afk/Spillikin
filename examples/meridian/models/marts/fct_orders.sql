select
    t.order_id,
    t.customer_id,
    t.order_date,
    t.line_count,
    t.net_revenue_usd,
    t.net_revenue_usd - t.product_cost_usd as margin_usd,
    t.order_total_usd,
    coalesce(p.paid_usd, 0) as paid_usd,
    coalesce(p.paid_usd, 0) >= t.order_total_usd as is_fully_paid
from {{ ref('int_order_totals') }} as t
left join {{ ref('int_payments_by_order') }} as p
    on p.order_id = t.order_id
