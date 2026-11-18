from django.db.models import IntegerField, OuterRef, Subquery, Sum
from django.db.models.functions import Coalesce

from apps.orders.models import OrderItem, OrderStatus

# An order that never left, or came back, did not sell anything.
NOT_SOLD = (OrderStatus.CANCELLED, OrderStatus.RETURNED, OrderStatus.REFUNDED)


def units_sold_subquery(product_ref="pk"):
    """
    Jars of a product sold: the quantities on its order lines, across every
    order except the cancelled, returned and refunded ones. A pending order
    counts: it is a customer who bought, waiting to be confirmed.

    A correlated subquery rather than a join + Sum, so it can sit next to
    other annotations (counts over variants) without multiplying them.
    """
    lines = (
        OrderItem.objects.filter(product=OuterRef(product_ref))
        .exclude(order__status__in=NOT_SOLD)
        .order_by()
        .values("product")
        .annotate(total=Sum("quantity"))
        .values("total")
    )
    return Coalesce(Subquery(lines, output_field=IntegerField()), 0)
