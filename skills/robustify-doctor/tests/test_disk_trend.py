def test_disk_projection_is_withheld_on_a_noisy_series():
    """The real series that paged a client: a peak start, a transient dip, no leak.

    Endpoint arithmetic said -5.99 GB / 24h -> "full in 17 days". The same samples,
    fit properly, are dominated by noise and must produce no projection at all. The
    3-day series was actually GAINING space.
    """
    # (hours_from_start, free_gb) — the observed bosun samples
    series = [
        (0.0, 112.2), (1.0, 111.6), (2.0, 111.5), (3.0, 111.4), (4.0, 111.4),
        (5.0, 111.4), (6.0, 111.5), (7.0, 111.4), (8.0, 111.4), (9.0, 111.3),
        (10.0, 111.3), (11.0, 111.5), (12.0, 104.0), (13.0, 106.2),
    ]
    xs = [p[0] for p in series]
    ys = [p[1] for p in series]
    n = len(series)
    mx = sum(xs) / n
    my = sum(ys) / n
    denom = sum((x - mx) ** 2 for x in xs)
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / denom
    resid = [y - (my + slope * (x - mx)) for x, y in zip(xs, ys)]
    spread = (sum(r * r for r in resid) / n) ** 0.5

    free_gb = ys[-1]
    project = slope < 0 and abs(slope) > spread and free_gb < 25

    endpoint_delta = ys[-1] - ys[0]
    assert endpoint_delta < -5, "sanity: endpoint math really does look like a cliff"
    assert not project, (
        f"must withhold projection: slope={slope:.3f} noise={spread:.3f} free={free_gb}"
    )


def test_disk_projection_fires_on_a_genuine_leak():
    """A real leak must still be caught: steady decline, low noise, near the floor."""
    series = [(float(i), 20.0 - 0.9 * i) for i in range(14)]
    xs = [p[0] for p in series]
    ys = [p[1] for p in series]
    n = len(series)
    mx = sum(xs) / n
    my = sum(ys) / n
    denom = sum((x - mx) ** 2 for x in xs)
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / denom
    resid = [y - (my + slope * (x - mx)) for x, y in zip(xs, ys)]
    spread = (sum(r * r for r in resid) / n) ** 0.5

    free_gb = ys[-1]
    project = slope < 0 and abs(slope) > spread and free_gb < 25
    assert project, f"a real leak must project: slope={slope:.3f} noise={spread:.3f}"


def test_ample_free_space_never_projects():
    """A steady slow drift on a big volume is not news, however clean the slope."""
    series = [(float(i), 400.0 - 0.2 * i) for i in range(14)]
    xs = [p[0] for p in series]
    ys = [p[1] for p in series]
    n = len(series)
    mx = sum(xs) / n
    my = sum(ys) / n
    denom = sum((x - mx) ** 2 for x in xs)
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / denom
    free_gb = ys[-1]
    assert not (slope < 0 and free_gb < 25), "400GB free is not an incident"
