"""Reading the graph — the router, against a real Neo4j.

These need a database and skip without one (`conftest.neo4j_session` says why).
That is deliberate and it is the honest place to draw the line: what `query.py`
contains is Cypher, and a stub session that answered Cypher would be a second,
wrong implementation of Neo4j that every one of these tests would pass against.

What they pin is `docs/KNOWLEDGE_GRAPH.md` §9.1 — **ask about a table and you get
its neighbours back**, each with what links it, the measured pairs inside the
neighbour they connect to; a route is searched over one query's links; and
nothing comes back ranked (§6.1). How those answers are cut into the agent's
cards is `test_graph_navigation.py`'s, with no database.
"""

from __future__ import annotations

import csv
from pathlib import Path

import pytest
import yaml

from portia import catalog
from portia.knowledge import build_graph, query, schema, store


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """Two sources, a group, and two specs — one reading the other."""
    data = tmp_path / "data"
    data.mkdir()
    with open(data / "orders.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["order_id", "customer_id", "amount"])
        w.writerows([[1, " C1 ", 10], [2, "C2", 20]])
    with open(data / "customers.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["customer_id", "name"])
        w.writerows([["C1", "Ann"], ["C2", "Bo"]])

    # Indexed, read by no spec and compared to nothing — the state most of a
    # real project is in, and the one `connected_columns` exists to shrink.
    with open(data / "returns.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["return_id", "customer_id", "reason"])
        w.writerows([[1, "C1", "damaged"], [2, "C9", "late"]])

    portia_dir = tmp_path / catalog.DEFAULT_DIR
    catalog.init_project("a shop", portia_dir=portia_dir)
    for name in ("orders", "customers", "returns"):
        catalog.index_source(data / f"{name}.csv", portia_dir=portia_dir)
    catalog.set_group(
        "sales",
        context="what the shop exports",
        sources=["orders", "customers"],
        portia_dir=portia_dir,
    )

    specs = tmp_path / "specs"
    specs.mkdir()
    (specs / "stg_orders.yaml").write_text(
        yaml.safe_dump(
            {
                "version": 1,
                "sources": {"orders": "data/orders.csv"},
                "steps": [
                    {
                        "id": "cleaned",
                        "op": "normalize",
                        "input": "orders",
                        "transforms": [{"column": "customer_id", "op": "strip"}],
                    }
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (specs / "mart_orders.yaml").write_text(
        yaml.safe_dump(
            {
                "version": 1,
                "sources": {"customers": "data/customers.csv"},
                "steps": [
                    {
                        "id": "joined",
                        "op": "join",
                        "left": "stg_orders",
                        "right": "customers",
                        "keys": ["customer_id"],
                    }
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return tmp_path


@pytest.fixture
def pid(project) -> str:
    """Which project every question below is asked of (`schema.PROJECT`).

    One server holds them all, so this is not ceremony: without it `lookup`
    would resolve a table belonging to whatever else is in the database.
    """
    return schema.project_id(project)


@pytest.fixture
def filled(neo4j_session, project):
    """The project, in the database — the state after `cli.knowledge --write`."""
    store.write(build_graph(project).graph, neo4j_session)
    return neo4j_session


# --- asking about a table ---------------------------------------------------


def _neighbour(answer: dict, name: str) -> dict:
    (found,) = [n for n in answer["neighbours"] if n["name"] == name]
    return found


def test_a_source_answers_with_the_models_that_read_it(filled, pid):
    answer = query.lookup(filled, "orders", project=pid)
    assert answer["table"]["kind"] == "Source"
    assert answer["table"]["n_columns"] == 3
    assert [n["name"] for n in answer["neighbours"] if n["builds"]] == ["stg_orders"]
    assert not any(n["built_from"] for n in answer["neighbours"])


def test_a_table_linked_to_nothing_has_no_neighbours(filled, pid):
    """Nothing reads `returns`, it is in no group and nothing was compared to
    it. `n_columns` still says the table has three, so an empty list reads as
    *nothing connects here* rather than as *this table has no columns*."""
    answer = query.lookup(filled, "returns", project=pid)
    assert answer["table"]["n_columns"] == 3
    assert answer["neighbours"] == []


def test_a_model_answers_with_what_it_reads_including_another_model(filled, pid):
    answer = query.lookup(filled, "mart_orders", project=pid)
    assert answer["table"]["kind"] == "Model"
    assert [(n["kind"], n["name"]) for n in answer["neighbours"] if n["built_from"]] == [
        ("Source", "customers"),
        ("Model", "stg_orders"),
    ]


def test_a_source_can_be_named_by_its_path(filled, pid):
    """A path is what identifies a Source, and what a spec writes down."""
    assert query.lookup(filled, "data/orders.csv", project=pid)["table"]["name"] == "orders"


def test_a_group_is_named_once_and_its_members_are_neighbours(filled, pid):
    answer = query.lookup(filled, "orders", project=pid)
    assert answer["groups"] == [
        {"name": "sales", "context": "what the shop exports", "n_members": 2}
    ]
    assert _neighbour(answer, "customers")["groups"] == ["sales"]


def test_a_table_answer_does_not_restate_describe_source(filled, pid):
    """The whole column list is `describe_source`'s job and cost 73% of this
    answer when it was here — 5,091 of 7,015 characters on AQN's `stations`.

    And `connected_columns`, the names of the columns carrying any link, is gone
    too (2026-10-13): on a table whose columns are all lineage it was every
    column, and a column that matters to a neighbour is in that neighbour's pairs.
    """
    answer = query.lookup(filled, "orders", project=pid)
    assert set(answer) == {"table", "neighbours", "groups"}
    # And nothing of the pipeline's own vocabulary: `layer` groups the project
    # canvas and says nothing about what a table is *to* another table.
    assert set(answer["table"]) == {"kind", "name", "path", "n_columns"}


def test_nothing_is_measured_yet_so_no_neighbour_has_a_pair(filled, pid):
    """Phase B reads; phase C measures. A pair here would be fabricated."""
    assert all(n["pairs"] == [] for n in query.lookup(filled, "orders", project=pid)["neighbours"])


def _measure(session, pid, left_col, right_col, reason, numbers):
    """One measured pair between the two fixture sources."""
    from portia.knowledge import measure
    from portia.knowledge.schema import SOURCE, Ref

    orders, customers = Ref(SOURCE, "data/orders.csv"), Ref(SOURCE, "data/customers.csv")
    store.write_measured(
        [
            measure.overlap_edge(
                measure.Pair(orders, left_col, customers, right_col, reason), numbers
            )
        ],
        session,
        pid,
    )


def test_a_measurement_comes_back_inside_the_neighbour_it_links(filled, pid):
    """It used to say `overlaps customers (1 measured pair(s))`, and reaching the
    pair meant already knowing which column to name — so the 18 measurements in
    the AQN project were, in practice, write-only. Now the pairs sit in the
    neighbour they connect to, with the numbers and the sentence the agent wrote
    when it asked.
    """
    _measure(
        filled,
        pid,
        "customer_id",
        "customer_id",
        "orders should reference the customer list",
        {"n_shared_values": 2, "left_coverage": 1.0, "right_coverage": 0.5},
    )

    (pair,) = _neighbour(query.lookup(filled, "orders", project=pid), "customers")["pairs"]
    assert pair["n_shared_values"] == 2
    assert pair["asked_because"] == "orders should reference the customer list"
    assert pair["origin"] == "measured"
    # `None`, not `False`: this measurement recorded no fingerprints, so whether
    # it still matches the files cannot be told — and null says *cannot tell*
    # where `False` would say *fine* (§4.5).
    assert pair["stale"] is None


def test_a_pair_faces_the_table_asked_about(filled, pid):
    """The edge points from orders; asked from customers, the coverages turn."""
    _measure(
        filled,
        pid,
        "customer_id",
        "customer_id",
        "orders should reference the customer list",
        {"n_shared_values": 2, "left_coverage": 1.0, "right_coverage": 0.5},
    )
    from_orders = _neighbour(query.lookup(filled, "orders", project=pid), "customers")["pairs"][0]
    from_customers = _neighbour(query.lookup(filled, "customers", project=pid), "orders")["pairs"][
        0
    ]
    assert (from_orders["coverage"], from_orders["other_coverage"]) == (1.0, 0.5)
    assert (from_customers["coverage"], from_customers["other_coverage"]) == (0.5, 1.0)


def test_each_end_of_a_pair_carries_what_makes_its_number_readable(filled, pid):
    """A zero is unreadable without them, and two zeros are indistinguishable.

    On AQN, `LOCATION_CODE` (4 distinct) against `VALUE` (56) sharing nothing is
    close to a non-event, while 179 country names against 16 sharing nothing is
    the mapping job. Same number, same shape, different findings — and the
    distinct counts are the whole difference.
    """
    _measure(
        filled,
        pid,
        "customer_id",
        "customer_id",
        "the padded key against the clean one",
        {"n_shared_values": 0, "left_coverage": 0.0, "right_coverage": 0.0},
    )

    (pair,) = query.lookup(filled, "orders", neighbour="customers", project=pid)["pairs"]
    assert pair["column"] == "customer_id"
    assert pair["column_facts"]["n_distinct"] == 2
    # And the value that explains the zero: ' C1 ' is padded, 'C1' is not.
    assert pair["column_facts"]["top"] == " C1 "
    assert pair["other_facts"]["top"] == "C1"


def test_a_measurement_does_not_ship_the_machinery_that_computed_it(filled, pid):
    """`properties(r)` wholesale sent the project's absolute path and both
    fingerprints to the model on every edge. The fingerprints exist so `stale`
    can be worked out, and `stale` is already in the same answer."""
    _measure(
        filled,
        pid,
        "customer_id",
        "customer_id",
        "same key",
        {"n_shared_values": 2, "left_coverage": 1.0, "right_coverage": 1.0},
    )

    (pair,) = _neighbour(query.lookup(filled, "orders", project=pid), "customers")["pairs"]
    assert not {"project", "left_fingerprint", "right_fingerprint"} & set(pair)


# --- between two tables -----------------------------------------------------


def test_between_two_tables_is_every_link_and_every_pair(filled, pid):
    _measure(
        filled,
        pid,
        "customer_id",
        "customer_id",
        "same key",
        {"n_shared_values": 2, "left_coverage": 1.0, "right_coverage": 1.0},
    )
    answer = query.lookup(filled, "orders", neighbour="customers", project=pid)
    assert answer["neighbour"]["name"] == "customers"
    assert (answer["built_from"], answer["builds"], answer["groups"]) == (False, False, ["sales"])
    assert [(p["column"], p["other_column"]) for p in answer["pairs"]] == [
        ("customer_id", "customer_id")
    ]
    assert answer["joins"] == []

    built = query.lookup(filled, "stg_orders", neighbour="orders", project=pid)
    assert built["built_from"] and not built["builds"] and built["pairs"] == []


def test_a_table_is_not_its_own_neighbour(filled, pid):
    with pytest.raises(ValueError, match="different neighbour"):
        query.lookup(filled, "orders", neighbour="data/orders.csv", project=pid)


# --- routes -----------------------------------------------------------------


def test_a_route_runs_through_the_models_built_from_both_tables(filled, pid):
    """orders builds stg_orders, from which and customers mart_orders is built."""
    answer = query.lookup(filled, "orders", to="customers", project=pid)
    assert answer["n_routes"] == 1
    (route,) = answer["routes"]
    assert route["tables"] == ["orders", "stg_orders", "mart_orders", "customers"]
    assert [(s["built_from"], s["builds"]) for s in route["steps"]] == [
        (False, True),
        (False, True),
        (True, False),
    ]


def _join(session, pid, left_cols, right_cols, *, origin="join_check"):
    """A join record between the two fixture sources, orders to customers."""
    from portia.knowledge import measure
    from portia.knowledge.schema import SOURCE, Ref

    report = {
        "keys": {"left": left_cols, "right": right_cols},
        "relationship": "many:1",
        "key_dtype_match": True,
        "overlap": {"n_shared_keys": 2, "left_coverage": 1.0, "right_coverage": 0.5},
        "left": {"max_key_multiplicity": 2},
        "right": {"max_key_multiplicity": 1},
    }
    edge = measure.join_edge(
        Ref(SOURCE, "data/orders.csv"),
        Ref(SOURCE, "data/customers.csv"),
        report,
        origin=origin,
        asked_because="orders name customers",
    )
    store.write_measured([edge], session, pid)


def test_a_join_is_the_shortest_route_and_reads_from_the_side_walked(filled, pid):
    _join(filled, pid, ["customer_id"], ["customer_id"])
    answer = query.lookup(filled, "customers", to="orders", project=pid)
    assert [r["tables"] for r in answer["routes"]] == [
        ["customers", "orders"],
        ["customers", "mart_orders", "stg_orders", "orders"],
    ]
    (step,) = answer["routes"][0]["steps"]
    (join,) = step["joins"]
    # Walked from customers, against the record's direction: turned to face it.
    assert (join["relationship"], join["repeats"], join["other_repeats"]) == ("1:many", 1, 2)
    assert (join["coverage"], join["other_coverage"]) == (0.5, 1.0)


def test_a_comparison_is_never_a_route_however_much_it_shares(filled, pid):
    """Two hourly tables share every hour; nobody joins them on it."""
    _measure(
        filled,
        pid,
        "customer_id",
        "customer_id",
        "same key",
        {"n_shared_values": 2, "left_coverage": 1.0, "right_coverage": 1.0},
    )
    answer = query.lookup(filled, "orders", to="customers", project=pid)
    assert [len(r["steps"]) for r in answer["routes"]] == [3]


def test_with_no_route_the_answer_lists_what_was_compared_on_either_table(filled, pid):
    _measure(
        filled,
        pid,
        "customer_id",
        "customer_id",
        "same key",
        {"n_shared_values": 2, "left_coverage": 1.0, "right_coverage": 0.5},
    )
    answer = query.lookup(filled, "returns", to="orders", project=pid)
    assert answer["n_routes"] == 0
    (row,) = answer["compared"]
    assert (row["table"], row["column"], row["other_table"]) == (
        "orders",
        "customer_id",
        "customers",
    )


def test_a_join_comes_back_inside_its_neighbour_before_the_comparisons(filled, pid):
    _join(filled, pid, ["customer_id"], ["customer_id"])
    neighbour = _neighbour(query.lookup(filled, "orders", project=pid), "customers")
    (join,) = neighbour["joins"]
    assert (join["columns"], join["origin"], join["asked_because"]) == (
        ["customer_id"],
        "join_check",
        "orders name customers",
    )
    between = query.lookup(filled, "customers", neighbour="orders", project=pid)
    assert between["joins"][0]["relationship"] == "1:many"
    column = query.lookup(filled, "orders", "customer_id", project=pid)
    assert [j["table"] for j in column["joins"]] == ["customers"]


def test_a_table_unreachable_in_the_steps_allowed_has_no_route(filled, pid):
    assert query.lookup(filled, "returns", to="orders", project=pid)["n_routes"] == 0


# --- asking about a column --------------------------------------------------


def test_a_column_says_which_column_it_came_from_and_which_step_explains_it(filled, pid):
    answer = query.lookup(filled, "stg_orders", "customer_id", project=pid)
    assert answer["upstream"] == [
        {
            "hops": 1,
            "kind": "Source",
            "table": "orders",
            "path": "data/orders.csv",
            "column": "customer_id",
            "via": "normalize",
            "step": "stg_orders#cleaned",
        }
    ]


def test_lineage_walks_through_a_model_to_the_file_underneath(filled, pid):
    """Hops each way, nearest first; `origins` is where it bottoms out."""
    answer = query.lookup(filled, "mart_orders", "amount", project=pid)
    assert [(r["hops"], r["table"]) for r in answer["upstream"]] == [
        (1, "stg_orders"),
        (2, "orders"),
    ]
    assert answer["origins"] == [
        {"kind": "Source", "table": "orders", "path": "data/orders.csv", "column": "amount"}
    ]


def test_a_shared_key_shows_both_origins(filled, pid):
    """`coalesce(l.k, r.k)` — two edges, each with the step explaining its side."""
    answer = query.lookup(filled, "mart_orders", "customer_id", project=pid)
    assert {(r["table"], r["via"]) for r in answer["upstream"] if r["hops"] == 1} == {
        ("stg_orders", "join"),
        ("customers", "join"),
    }
    assert {r["table"] for r in answer["origins"]} == {"orders", "customers"}


def test_a_source_column_says_what_is_built_from_it_hops_downstream(filled, pid):
    """The forward direction — what §4.5 needs to answer 'this file changed, so what'."""
    downstream = query.lookup(filled, "orders", "amount", project=pid)["downstream"]
    assert [(r["hops"], r["table"], r["column"]) for r in downstream] == [
        (1, "stg_orders", "amount"),
        (2, "mart_orders", "amount"),
    ]


def test_a_column_carries_the_catalogs_facts_and_not_a_new_measurement(filled, pid):
    column = query.lookup(filled, "orders", "amount", project=pid)["column"]
    assert column["n_distinct"] == 2
    assert column["role"] is None


# --- misses -----------------------------------------------------------------


def test_an_unknown_table_names_its_near_misses_and_not_every_table(filled, pid):
    with pytest.raises(ValueError, match="closest: mart_orders"):
        query.lookup(filled, "mart_order", project=pid)
    with pytest.raises(ValueError, match="none named like it"):
        query.lookup(filled, "zzz", project=pid)


def test_an_unknown_column_names_its_near_misses_and_not_every_column(filled, pid):
    """Every name was one error longer than a harness passes whole, on a wide table."""
    with pytest.raises(ValueError, match="which has 3 — closest: amount"):
        query.lookup(filled, "orders", "AMOUNT", project=pid)
    with pytest.raises(ValueError, match="none named like it"):
        query.lookup(filled, "orders", "zzz", project=pid)


def test_one_question_at_a_time(filled, pid):
    with pytest.raises(ValueError, match="one question at a time"):
        query.lookup(filled, "orders", "amount", to="customers", project=pid)


def test_an_empty_graph_says_to_build_it_rather_than_that_the_table_is_missing(neo4j_session, pid):
    """Two very different misses, and they ask for different next moves."""
    with pytest.raises(ValueError, match="is empty"):
        query.lookup(neo4j_session, "orders", project=pid)


# --- the picture ------------------------------------------------------------


def test_the_subgraph_is_tables_until_you_ask_for_columns(filled, pid):
    """The one query here that deliberately returns a lot.

    Every other one is shaped by "a router that returns fifty things has not
    routed" — a rule about what an *agent* can act on. A picture is read by a
    person, who copes with far more at once, so the constraint is different and
    the function is separate rather than the router being loosened.
    """
    tables = query.subgraph(filled, project=pid)
    assert {n["kind"] for n in tables["nodes"]} == {"Source", "Model", "Group"}
    # What the Columns view's count says about tables, when the cap cut some.
    assert tables["tables"] == {"shown": len(tables["nodes"]), "total": len(tables["nodes"])}

    columns = query.subgraph(filled, columns=True, project=pid)
    assert "Column" in {n["kind"] for n in columns["nodes"]}
    assert len(columns["nodes"]) > len(tables["nodes"])


def test_every_table_keeps_columns_and_a_more_node_counts_the_rest(filled, pid, monkeypatch):
    """A project of 18 sources drew 550 columns of one table and none of sixteen.

    The cap took columns in key order, and a key starts with its table's path,
    so the first table alphabetically took the picture. Now every table shows
    its connected columns and then its first ``N`` in file order, and a table
    that lost some hangs one `More` node saying how many — with ``N`` squeezed
    to one here so the fixture's three-column tables have something to lose.
    """
    from portia.knowledge.schema import HAS_COLUMN

    monkeypatch.setattr(query, "MIN_COLUMNS_SHOWN", 1)
    monkeypatch.setattr(query, "MAX_COLUMNS_SHOWN", 1)
    picture = query.subgraph(filled, columns=True, project=pid)
    nodes = {n["id"]: n for n in picture["nodes"]}
    hanging: dict[str, list[dict]] = {}
    for e in picture["edges"]:
        if e["kind"] == HAS_COLUMN:
            hanging.setdefault(nodes[e["from"]]["label"], []).append(nodes[e["to"]])

    # Nothing reads `returns` and nothing was compared to it: one column, the
    # first in the file — `return_id`, where name order would say `customer_id`.
    returns = hanging["returns"]
    assert [n["label"] for n in returns if n["kind"] == "Column"] == ["return_id"]
    [more] = [n for n in returns if n["kind"] == query.MORE]
    assert more["label"] == "+2 more"
    assert more["properties"] == {
        "table": "returns",
        "table_kind": "Source",
        "n_more": 2,
        "n_columns": 3,
    }

    # Every column of `orders` feeds `stg_orders`, so every one is connected
    # and drawn, whatever `N` is, and the table has nothing left to count.
    assert sorted(n["label"] for n in hanging["orders"]) == ["amount", "customer_id", "order_id"]

    drawn = [n for n in picture["nodes"] if n["kind"] == "Column"]
    attached = {e["to"] for e in picture["edges"] if e["kind"] == HAS_COLUMN}
    assert {n["id"] for n in drawn} <= attached, "every drawn column hangs from its table"
    assert picture["columns"]["shown"] == len(drawn)
    assert picture["columns"]["per_table"] == 1
    assert not picture["columns"]["connected_cut"]


def test_the_picture_speaks_portias_vocabulary_and_no_librarys(filled, pid):
    """Swapping what draws it must be one JavaScript file and nothing else."""
    node = query.subgraph(filled, project=pid)["nodes"][0]
    assert set(node) == {"id", "kind", "label", "properties"}
    edge = query.subgraph(filled, project=pid)["edges"][0]
    assert set(edge) == {"from", "to", "kind", "properties"}


def test_table_level_overlap_is_derived_rather_than_stored(neo4j_session, project, pid):
    """§4.1 rejected a stored source-to-source summary edge — two things would
    state one fact and could disagree — and said to derive it in the query."""
    from portia.knowledge import measure
    from portia.knowledge.schema import SOURCE, Ref

    store.write(build_graph(project).graph, neo4j_session)
    orders, customers = Ref(SOURCE, "data/orders.csv"), Ref(SOURCE, "data/customers.csv")
    store.write_measured(
        [
            measure.overlap_edge(
                measure.Pair(orders, "customer_id", customers, "customer_id", "same key"),
                {"n_shared_values": 2, "left_coverage": 1.0, "right_coverage": 1.0},
            )
        ],
        neo4j_session,
        pid,
    )

    edges = [
        e for e in query.subgraph(neo4j_session, project=pid)["edges"] if e["kind"] == "OVERLAPS"
    ]
    assert len(edges) == 1
    assert edges[0]["properties"] == {"n_measured_pairs": 1}


def test_a_column_with_nothing_underneath_it_says_so_instead_of_looking_like_a_file(
    neo4j_session, project, pid
):
    """`docs/SQL_LINEAGE.md` §1.4 — the trail ending and the data starting are
    the same shape in the graph, and only the marker tells them apart.

    Without it `origins` reports `count(*)` as the place the data came from,
    which is the graph asserting something false rather than staying silent.
    """
    (project / "specs" / "agg_orders.yaml").write_text(
        yaml.safe_dump(
            {
                "version": 1,
                "sources": {"orders": "data/orders.csv"},
                "steps": [
                    {
                        "id": "totals",
                        "op": "sql",
                        "inputs": ["orders"],
                        "sql": "SELECT customer_id, count(*) AS n FROM orders GROUP BY 1",
                    }
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    store.write(build_graph(project).graph, neo4j_session)

    counted = query.lookup(neo4j_session, "agg_orders", "n", project=pid)
    assert counted["column"]["derivation"] == "unknown"
    assert counted["upstream"] == []
    assert "nothing readable underneath it" in query.render_text(counted)

    # The column beside it resolved, and says nothing about derivation at all.
    carried = query.lookup(neo4j_session, "agg_orders", "customer_id", project=pid)
    assert carried["column"].get("derivation") is None
    assert carried["origins"] == [
        {"kind": "Source", "table": "orders", "path": "data/orders.csv", "column": "customer_id"}
    ]


def test_a_model_whose_sql_could_not_be_read_says_not_traced_and_why(neo4j_session, project, pid):
    """Never *computed here*: an unreadable unpivot carries its key columns through
    unchanged, and the copilot was told to read every one of them as computed
    (2026-10-15). The model says which code stopped it and at which step."""
    from portia import pipeline

    (project / "specs" / "long_orders.yaml").write_text(
        yaml.safe_dump(
            {
                "version": 1,
                "sources": {"orders": "data/orders.csv"},
                "steps": [
                    {
                        "id": "unpivot",
                        "op": "sql",
                        "inputs": ["orders"],
                        "sql": "SELECT * FROM orders UNPIVOT (v FOR field IN (amount))",
                    }
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    pipeline.build_project(project)
    store.write(build_graph(project).graph, neo4j_session)

    answer = query.lookup(neo4j_session, "long_orders", "order_id", project=pid)
    assert answer["column"]["derivation"] == "not_traced"
    assert answer["column"]["lineage_unread"] == "pivot"
    assert answer["column"]["lineage_unread_step"] == "unpivot"
    assert "lineage not read: pivot at step unpivot" in query.render_text(answer)
    card = query.lookup(neo4j_session, "long_orders", project=pid)["table"]
    assert card["lineage_unread"] == "pivot"
    # A source, or a model read whole, carries neither key.
    assert "lineage_unread" not in query.lookup(neo4j_session, "orders", project=pid)["table"]


def test_a_trail_that_ends_at_an_unreadable_column_still_reports_that_column(
    neo4j_session, project, pid
):
    """It is returned marked rather than filtered out: an origins list that
    silently loses its last rung reads as *this came from nowhere*."""
    (project / "specs" / "agg_orders.yaml").write_text(
        yaml.safe_dump(
            {
                "version": 1,
                "sources": {"orders": "data/orders.csv"},
                "steps": [
                    {
                        "id": "totals",
                        "op": "sql",
                        "inputs": ["orders"],
                        "sql": "SELECT customer_id, count(*) AS n FROM orders GROUP BY 1",
                    }
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (project / "specs" / "mart_totals.yaml").write_text(
        yaml.safe_dump(
            {
                "version": 1,
                "sources": {},
                "steps": [
                    {
                        "id": "cleaned",
                        "op": "normalize",
                        "input": "agg_orders",
                        "transforms": [],
                    }
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    store.write(build_graph(project).graph, neo4j_session)

    assert query.lookup(neo4j_session, "mart_totals", "n", project=pid)["origins"] == [
        {
            "kind": "Model",
            "table": "agg_orders",
            "path": None,
            "column": "n",
            "derivation": "unknown",
        }
    ]


# --- choosing the picture's columns, without a database ----------------------
#
# `choose_columns` is pure on purpose (Cypher fetches, Python chooses), so the
# rule is pinned here with no server: the cases above that need one are about
# whether the Cypher feeding it is right.


def _table(name: str, n: int, *, connected=(), positions: bool = True) -> list[query.ColumnRow]:
    """``n`` columns of table ``name``; those numbered in ``connected`` link to something."""
    return [
        query.ColumnRow(
            id=f"{name}:{i}",
            table=name,
            key=f"source:{name}::c{i:04d}",
            position=i if positions else None,
            connected=i in connected,
        )
        for i in range(1, n + 1)
    ]


def _drawn(choice: query.ColumnChoice, table: str) -> list[int]:
    return [int(i.split(":")[1]) for i in choice.ids if i.startswith(f"{table}:")]


def test_a_table_no_wider_than_n_shows_every_column():
    choice = query.choose_columns(["narrow", "wide"], [*_table("narrow", 3), *_table("wide", 40)])
    assert choice.per_table == query.MAX_COLUMNS_SHOWN
    assert _drawn(choice, "narrow") == [1, 2, 3]
    assert _drawn(choice, "wide") == list(range(1, 16))
    assert choice.left_out == {"wide": 25}, "a table that lost nothing has no More node"
    assert choice.sizes == {"narrow": 3, "wide": 40}
    assert (choice.total, len(choice.ids)) == (43, 18)


def test_connected_columns_come_first_and_the_rest_in_file_order():
    """Two linked columns far down a wide table are drawn, and the table's
    first columns fill it up to ``N`` around them."""
    choice = query.choose_columns(["wide"], _table("wide", 40, connected=(30, 35)))
    assert _drawn(choice, "wide") == [*range(1, 14), 30, 35]
    assert choice.connected == choice.connected_shown == 2
    assert choice.left_out == {"wide": 25}


def test_a_table_with_more_connected_columns_than_n_shows_all_of_them():
    choice = query.choose_columns(["linked"], _table("linked", 30, connected=range(1, 21)))
    assert _drawn(choice, "linked") == list(range(1, 21))
    assert choice.left_out == {"linked": 10}


def test_file_order_is_the_position_and_falls_back_to_the_key():
    """Never the name when a position exists, and never a measured number at
    all: a `ColumnRow` carries nothing a ranking could be made of."""
    backwards = [
        query.ColumnRow(id=f"t:{i}", table="t", key=f"k{20 - i:02d}", position=i)
        for i in range(1, 21)
    ]
    assert _drawn(query.choose_columns(["t"], backwards), "t") == list(range(1, 16))

    # A graph built before positions existed: the key's order, which within a
    # table is the column's name.
    unplaced = [query.ColumnRow(id=f"t:{i}", table="t", key=f"k{20 - i:02d}") for i in range(1, 21)]
    assert _drawn(query.choose_columns(["t"], unplaced), "t") == list(range(20, 5, -1))


@pytest.mark.parametrize(
    ("cap", "n_plain", "n_connected", "expected"),
    [
        (600, 17, 4, 15),  # 18 tables: (600 - 18 - 4) // 18 = 32, held at the most
        (100, 10, 0, 9),  # 10 tables: (100 - 10) // 10 = 9, between the two
        (100, 9, 10, 8),  # 10 tables: (100 - 10 - 10) // 10, the linked ones first
        (30, 10, 0, 5),  # 10 tables: (30 - 10) // 10 = 2, held at the fewest
    ],
)
def test_n_is_the_room_left_shared_evenly_between_five_and_fifteen(
    cap, n_plain, n_connected, expected
):
    """``n_plain`` tables of fifty columns, and one more whose ``n_connected``
    columns all link to something when there are any."""
    tables = [f"t{i}" for i in range(n_plain)]
    rows = [row for name in tables for row in _table(name, 50)]
    if n_connected:
        tables.append("linked")
        rows += _table("linked", n_connected, connected=range(1, n_connected + 1))
    assert query.choose_columns(tables, rows, cap=cap).per_table == expected


def test_connected_columns_that_do_not_fit_are_shared_evenly_and_say_so():
    """Each table may draw the same number of its connected columns, the most
    that fits; a table with fewer leaves its spare to the rest."""
    rows = [
        *_table("many", 50, connected=range(1, 51)),
        *_table("few", 3, connected=(1, 2, 3)),
        *_table("none", 10),
    ]
    choice = query.choose_columns(["many", "few", "none"], rows, cap=40)
    # 37 places after the three tables: `few` takes 3 and `none` its five, so
    # `many` may take 29, and the picture is exactly the cap.
    assert choice.connected_per_table == 29
    assert _drawn(choice, "many") == list(range(1, 30))
    assert _drawn(choice, "few") == [1, 2, 3]
    assert choice.per_table == query.MIN_COLUMNS_SHOWN, "no room is left, so the fewest"
    assert _drawn(choice, "none") == [1, 2, 3, 4, 5]
    assert choice.connected_cut and (choice.connected, choice.connected_shown) == (53, 32)
    assert choice.left_out == {"many": 21, "none": 5}
    assert len(choice.ids) + 3 == 40


def test_the_picture_never_holds_more_than_the_cap():
    """Regression (2026-10-08): two models whose 1,579 columns were all lineage
    drew 698 nodes past a cap of 600, because every other table's floor of five
    was added on top of their share, and a picture that size never came to rest."""
    tables = ["wide_a", "wide_b", *(f"t{i:02d}" for i in range(18))]
    rows = [
        *_table("wide_a", 1579, connected=range(1, 1580)),
        *_table("wide_b", 1579, connected=range(1, 1580)),
        *(row for name in tables[2:] for row in _table(name, 40)),
    ]
    choice = query.choose_columns(tables, rows)

    assert len(choice.ids) + len(tables) <= query.MAX_GRAPH_NODES
    assert all(len(_drawn(choice, name)) == query.MIN_COLUMNS_SHOWN for name in tables[2:])
    assert len(_drawn(choice, "wide_a")) == len(_drawn(choice, "wide_b")) > 200


def test_a_column_no_table_lists_is_drawn_and_counts_nothing():
    """A measurement keeps a column alive after its file dropped it
    (`store.py`). It is connected by definition, so it is drawn, and it has no
    table to hang a More node from."""
    orphan = query.ColumnRow(id="lost", table=None, key="source:gone.csv::id", connected=True)
    choice = query.choose_columns(["t"], [*_table("t", 3), orphan])
    assert "lost" in choice.ids
    assert None not in choice.sizes and None not in choice.left_out
    assert choice.connected == 1


def test_a_column_whose_table_is_not_drawn_is_not_drawn():
    choice = query.choose_columns(["t"], [*_table("t", 3), *_table("cut", 4)])
    assert _drawn(choice, "cut") == []
    assert choice.total == 7


def test_the_choice_does_not_depend_on_the_order_rows_arrive_in():
    import random

    rows = [*_table("a", 30, connected=(7, 22)), *_table("b", 12), *_table("c", 25)]
    first = query.choose_columns(["a", "b", "c"], rows)
    shuffled = list(rows)
    random.Random(4).shuffle(shuffled)
    assert query.choose_columns(["a", "b", "c"], shuffled) == first
