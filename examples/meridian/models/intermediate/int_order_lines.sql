select
    i.order_item_id,
    i.order_id,
    p.category,
    i.quantity,
    i.quantity * i.unit_price as line_revenue,
    i.quantity * p.unit_cost as line_cost
from {{ ref('stg_order_items') }} as i
join {{ ref('stg_products') }} as p
    on p.product_id = i.product_id
