"""The add-data pickers' tree: what a container says about the leaves under it.

One name in every warehouse fixture is upper case, because every real extract
is and no fixture used to be.
"""

from portia.ui import picktree
from portia.ui.picktree import ALL, NONE, SOME

LISTING = {
    "": ["ANALYTICS", "raw"],
    "ANALYTICS": ["STG_SALES", "STG_OPS"],
    "ANALYTICS.STG_SALES": [("ORDERS", "table"), ("order_lines", "table"), ("V_DAILY", "view")],
    "ANALYTICS.STG_OPS": [("SHIFTS", "table")],
}


def _warehouse(notes=None):
    return picktree.from_listing(LISTING, notes or {})


def _state(key, ticks, notes=None):
    return picktree.tally(picktree.find(_warehouse(notes), key), ticks).state


def test_a_schema_says_all_some_or_none_of_its_tables():
    sales = "ANALYTICS.STG_SALES"
    every = {f"{sales}.ORDERS", f"{sales}.order_lines", f"{sales}.V_DAILY"}

    assert _state(sales, set()) == NONE
    assert _state(sales, {f"{sales}.ORDERS"}) == SOME
    assert _state(sales, every) == ALL


def test_a_tick_in_one_schema_shows_on_the_database_above_both():
    """The complaint this module answers: a tick made in another schema was
    visible nowhere once you had left it."""
    ticks = {"ANALYTICS.STG_OPS.SHIFTS"}
    total = picktree.tally(picktree.find(_warehouse(), "ANALYTICS"), ticks)

    assert (total.ticked, total.of, total.state) == (1, 4, SOME)
    assert _state("ANALYTICS.STG_SALES", ticks) == NONE


def test_an_unlisted_container_is_never_all():
    """`raw` has not been asked about. Nothing knows what is in it, so the
    warehouse above it cannot read as wholly ticked, and it has no total."""
    raw = picktree.find(_warehouse(), "raw")
    assert not raw.listed and picktree.unlisted(raw) == ["raw"]

    half = {k: v for k, v in LISTING.items() if k != "ANALYTICS.STG_SALES"}
    (database, _raw) = picktree.from_listing(half, {})
    total = picktree.tally(database, {"ANALYTICS.STG_OPS.SHIFTS"})

    assert not total.complete and total.state == SOME
    assert picktree.unlisted(database) == ["ANALYTICS.STG_SALES"]


def test_a_table_already_in_scope_is_counted_apart_and_never_offered():
    sales = "ANALYTICS.STG_SALES"
    notes = {f"{sales}.ORDERS": "in scope"}
    node = picktree.find(_warehouse(notes), sales)

    assert picktree.leaves([node]) == [f"{sales}.V_DAILY", f"{sales}.order_lines"]
    total = picktree.tally(node, {f"{sales}.V_DAILY", f"{sales}.order_lines"})
    assert (total.ticked, total.of, total.fixed, total.state) == (2, 2, 1, ALL)


def test_a_schema_with_everything_already_in_has_nothing_to_tick():
    notes = {"ANALYTICS.STG_OPS.SHIFTS": "in scope"}
    total = picktree.tally(picktree.find(_warehouse(notes), "ANALYTICS.STG_OPS"), set())

    assert (total.of, total.fixed, total.state) == (0, 1, NONE)


def test_a_filter_matches_whatever_the_case_and_keeps_the_shape():
    shown = picktree.shown(_warehouse(), "order")
    (database, raw) = shown
    (schema,) = database.children

    assert [leaf.name for leaf in schema.children] == ["ORDERS", "order_lines"]
    assert raw.key == "raw", "an unlisted database stays: nothing has looked inside it"


def test_a_container_whose_own_name_matches_stays_whole():
    (database, _raw) = picktree.shown(_warehouse(), "stg_ops")

    assert [s.name for s in database.children] == ["STG_OPS"]
    assert [t.name for t in database.children[0].children] == ["SHIFTS"]


def test_a_filtered_box_speaks_for_the_rows_on_screen_and_the_count_for_all():
    """Tick the two the filter shows: the schema's box reads *all*, and its
    count, read off the whole tree, still says two of three."""
    sales = "ANALYTICS.STG_SALES"
    ticks = {f"{sales}.ORDERS", f"{sales}.order_lines"}
    on_screen = picktree.find(picktree.shown(_warehouse(), "order"), sales)

    assert picktree.tally(on_screen, ticks).state == ALL
    whole = picktree.tally(picktree.find(_warehouse(), sales), ticks)
    assert (whole.ticked, whole.of, whole.state) == (2, 3, SOME)


def test_a_view_says_it_is_one():
    node = picktree.find(_warehouse(), "ANALYTICS.STG_SALES.V_DAILY")
    assert node.detail == "view" and node.leaf


def test_ticks_are_counted_across_the_schemas_they_came_from():
    ticks = ["A.S1.T1", "A.S1.T2", "A.S2.T1", "B.S1.T1"]
    assert picktree.containers(ticks, ".") == 3


def test_files_nest_under_their_folders_below_the_data_folder():
    files = ["data/orders.csv", "data/2024/EVENTS.csv", "data/2024/q1/a.parquet", "data/2023/b.csv"]
    tree = picktree.from_paths(files, "data", {"data/2023/b.csv": "already indexed"})

    assert [(i.name, i.kind) for i in tree] == [
        ("2023", "folder"),
        ("2024", "folder"),
        ("orders.csv", picktree.FILE),
    ]
    year = picktree.find(tree, "data/2024")
    assert picktree.leaves([year]) == ["data/2024/q1/a.parquet", "data/2024/EVENTS.csv"]
    assert picktree.tally(picktree.find(tree, "data/2023"), set()).fixed == 1


def test_the_project_root_as_the_data_folder_has_no_prefix_to_strip():
    tree = picktree.from_paths(["a.csv", "sub/b.csv"], ".", {})

    assert [i.key for i in tree] == ["sub", "a.csv"]
    assert picktree.find(tree, "sub/b.csv").name == "b.csv"
