"""
A database sequence for order numbers.

`generate_order_number` used to read the highest number issued today and add
one. Two customers checking out in the same second read the same value, and the
second INSERT violated `order_number`'s unique constraint — one of them got a
500 on the single request that matters most.

A retry loop was the first fix and it was not good enough: under ten-way
simultaneity a thread can lose the race repeatedly, so the guarantee was
probabilistic. `nextval()` is the real answer. It takes no locks, so concurrent
checkouts never wait on each other, and it does not roll back, so two callers
can never receive the same value.

The cost of that is honest and worth stating: `nextval()` surviving a rollback
means an abandoned checkout burns a number, so the series has gaps, and the
counter is global rather than restarting at 1 each day. Order numbers stay the
same shape and length — ORD-YYYYMMDD-00001 — and stay unique and monotonic,
which is what they are actually for.

`setval` below starts the sequence above every sequence number already issued on
any date, so deploying this halfway through a trading day cannot collide with an
order placed that morning.
"""
from django.db import migrations

CREATE = """
CREATE SEQUENCE IF NOT EXISTS orders_order_number_seq AS bigint START WITH 1;

-- Start above the largest counter ever issued. Only rows in the canonical
-- ORD-YYYYMMDD-NNNNN shape are considered; anything else (hand-made rows,
-- fixtures) is ignored rather than crashing the cast.
SELECT setval(
    'orders_order_number_seq',
    GREATEST(
        (
            SELECT COALESCE(MAX(split_part(order_number, '-', 3)::bigint), 0)
            FROM orders_order
            WHERE order_number ~ '^ORD-[0-9]{8}-[0-9]+$'
        ),
        1
    )
);
"""

DROP = "DROP SEQUENCE IF EXISTS orders_order_number_seq;"


class Migration(migrations.Migration):

    dependencies = [
        ("orders", "0004_alter_order_payment_method"),
    ]

    operations = [
        migrations.RunSQL(sql=CREATE, reverse_sql=DROP),
    ]
