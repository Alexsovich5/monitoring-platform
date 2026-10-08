"""Read Zabbix item history straight from PostgreSQL.

Zabbix stores float items (value_type 0) in ``history`` and unsigned
items (value_type 3) in ``history_uint``.  Points are ``(value, clock)``
tuples with ``value`` as a float and ``clock`` in epoch seconds.
"""

TABLES = {0: 'history', 3: 'history_uint'}


class UnknownItem(LookupError):
    pass


class UnsupportedValueType(ValueError):
    pass


def table_for(value_type):
    """Return the history table that holds items of ``value_type``."""
    try:
        return TABLES[value_type]
    except (KeyError, TypeError):
        raise UnsupportedValueType('value_type %r has no numeric history '
                                   'table' % (value_type,))


def bucket(points, step):
    """Average ``points`` into ``step``-second buckets.

    Each bucket is labelled with its start clock (a multiple of ``step``);
    buckets without points are left out and the result is in clock order.
    """
    if step <= 0:
        raise ValueError('step must be positive, got %r' % (step,))
    sums = {}
    for value, clock in points:
        start = clock - clock % step
        total, count = sums.get(start, (0.0, 0))
        sums[start] = (total + value, count + 1)
    return [(total / count, start)
            for start, (total, count) in sorted(sums.items())]


def fetch(conn, itemid, start, end, step=None):
    """Return the points of ``itemid`` with ``start <= clock <= end`` in
    clock order, averaged into ``step``-second buckets when ``step`` is
    given.  Raises ``UnknownItem`` or ``UnsupportedValueType``."""
    cursor = conn.cursor()
    try:
        cursor.execute('SELECT value_type FROM items WHERE itemid = %s',
                       (itemid,))
        row = cursor.fetchone()
        if row is None:
            raise UnknownItem('no item with itemid %r' % (itemid,))
        table = table_for(row[0])
        # The table name comes from TABLES, never from the caller.
        cursor.execute('SELECT value, clock FROM %s '
                       'WHERE itemid = %%s AND clock BETWEEN %%s AND %%s '
                       'ORDER BY clock, ns' % table,
                       (itemid, start, end))
        points = [(float(value), clock) for value, clock in cursor.fetchall()]
    finally:
        cursor.close()
    if step:
        return bucket(points, step)
    return points
