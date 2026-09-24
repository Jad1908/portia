"""Reading the graph — the router, against a real Neo4j.

These need a database and skip without one (`conftest.neo4j_session` says why).
That is deliberate and it is the honest place to draw the line: what `query.py`
contains is Cypher, and a stub session that answered Cypher would be a second,
wrong implementation of Neo4j that every one of these tests would pass against.

What they pin is `docs/KNOWLEDGE_GRAPH.md` §9.1 and §7 — **ask about a table and
you get tables back**, not column pairs, because a router that returns fifty
things has not routed; and nothing comes back ranked (§6.1).
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


def test_a_source_answers_with_the_models_that_read_it(filled, pid):
    answer = query.lookup(filled, "orders", project=pid)
    assert answer["table"]["kind"] == "Source"
    assert [t["name"] for t in answer["read_by"]] == ["stg_orders"]
    assert answer["reads"] == []
    # Every column of `orders` feeds an `stg_orders` column, so every one of
    # them is connected. Lineage counts as a relationship; the filter only
    # drops columns that carry nothing at all.
    assert answer["table"]["n_columns"] == 3
    assert answer["connected_columns"] == ["amount", "customer_id", "order_id"]


def test_a_column_that_carries_nothing_is_not_named(filled, pid):
    """The filter that took AQN's `stations` from 191 columns to 4.

    Nothing reads `returns` and nothing has been compared to it, so neither of
    its columns has anything to say in a graph answer. `n_columns` still says
    the table has two, so an empty list reads as *nothing connects here* rather
    than as *this table has no columns*.
    """
    answer = query.lookup(filled, "returns", project=pid)
    assert answer["table"]["n_columns"] == 3
    assert answer["connected_columns"] == []


def test_a_model_answers_with_what_it_reads_including_another_model(filled, pid):
    answer = query.lookup(filled, "mart_orders", project=pid)
    assert answer["table"]["kind"] == "Model"
    assert [(t["kind"], t["name"]) for t in answer["reads"]] == [
        ("Source", "customers"),
        ("Model", "stg_orders"),
    ]


def test_a_source_can_be_named_by_its_path(filled, pid):
    """A path is what identifies a Source, and what a spec writes down."""
    assert query.lookup(filled, "data/orders.csv", project=pid)["table"]["name"] == "orders"


def test_a_group_comes_back_with_its_members(filled, pid):
    groups = query.lookup(filled, "orders", project=pid)["groups"]
    assert [g["name"] for g in groups] == ["sales"]
    assert sorted(groups[0]["members"]) == ["customers", "orders"]


def test_a_table_answer_does_not_restate_describe_source(filled, pid):
    """The whole column list is `describe_source`'s job and cost 73% of this
    answer when it was here — 5,091 of 7,015 characters on AQN's `stations`.

    What replaced it is the columns that carry a relationship, plus `n_columns`
    so a four-name list off a 191-column table cannot read as a small table.
    """
    answer = query.lookup(filled, "orders", project=pid)
    assert set(answer) == {
        "table",
        "connected_columns",
        "reads",
        "read_by",
        "groups",
        "overlaps",
        "precedents",
    }
    # And nothing of the pipeline's own vocabulary: `layer` groups the project
    # canvas and says nothing about what a table is *to* another table.
    assert set(answer["table"]) == {"kind", "name", "path", "summary", "n_columns"}


def test_nothing_is_measured_yet_so_there_is_nothing_to_overlap(filled, pid):
    """Phase B reads; phase C measures. An overlap here would be fabricated."""
    assert query.lookup(filled, "orders", project=pid)["overlaps"] == []


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


def test_a_measurement_comes_back_whole_rather_than_counted(filled, pid):
    """The reason this answer was reshaped.

    It used to say `overlaps customers (1 measured pair(s))`, and reaching the
    pair meant already knowing which column to name — so the 18 measurements in
    the AQN project were, in practice, write-only. Now the pairs are grouped
    under the neighbour they connect to, with the numbers and the sentence the
    agent wrote when it asked.
    """
    _measure(
        filled,
        pid,
        "customer_id",
        "customer_id",
        "orders should reference the customer list",
        {"n_shared_values": 2, "left_coverage": 1.0, "right_coverage": 1.0},
    )

    (neighbour,) = query.lookup(filled, "orders", project=pid)["overlaps"]
    assert neighbour["table"] == "customers"
    (pair,) = neighbour["pairs"]
    assert pair["n_shared_values"] == 2
    assert pair["asked_because"] == "orders should reference the customer list"
    # `None`, not `False`: this measurement recorded no fingerprints, so whether
    # it still matches the files cannot be told — and null says *cannot tell*
    # where `False` would say *fine* (§4.5).
    assert pair["stale"] is None


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

    (pair,) = query.lookup(filled, "orders", project=pid)["overlaps"][0]["pairs"]
    assert pair["column"]["name"] == "customer_id"
    assert pair["column"]["n_distinct"] == 2
    # And the value that explains the zero: ' C1 ' is padded, 'C1' is not.
    assert pair["column"]["top"] == " C1 "
    assert pair["other_column"]["top"] == "C1"


def test_a_pairing_the_project_already_made_is_offered_where_it_has_not(filled, pid):
    """The four measurements the 2026-08-31 AQN run should have taken and did not.

    Three tables carry `READING_ID` and were never compared to the event catalog,
    while two others' `READING_ID` had been measured against `AQN_READINGS.ID`
    at coverage 1.0. Nothing could say so, because every question had to name a
    table you already suspected.

    Here, `orders.customer_id` has been measured against `customers`, and
    `returns.customer_id` has not. The graph is not guessing that they are the
    same thing: it is replaying a pairing this project already asserted, with a
    reason, and measured.
    """
    _measure(
        filled,
        pid,
        "customer_id",
        "customer_id",
        "orders should reference the customer list",
        {"n_shared_values": 2, "left_coverage": 1.0, "right_coverage": 1.0},
    )

    precedents = query.lookup(filled, "returns", project=pid)["precedents"]

    # Two pointers off one measurement, and both are true: this project has
    # compared a column called `customer_id` to `customers` and to `orders`.
    # Which of those `returns.customer_id` belongs against is judgment, so both
    # are stated and neither is ranked. (On AQN only one side matches, because
    # the event catalog calls its key `ID` rather than `READING_ID`.)
    assert [(p["column"], p["against"]["table"], p["against"]["column"]) for p in precedents] == [
        ("customer_id", "customers", "customer_id"),
        ("customer_id", "orders", "customer_id"),
    ]
    (by,) = precedents[0]["measured_elsewhere_by"]
    assert by["table"] == "orders" and by["n_shared_values"] == 2
    assert by["asked_because"] == "orders should reference the customer list"


def test_a_column_you_have_already_measured_is_not_offered_as_a_gap(filled, pid):
    """A precedent is a gap, and a column you have looked at is not one."""
    _measure(
        filled,
        pid,
        "customer_id",
        "customer_id",
        "orders should reference the customer list",
        {"n_shared_values": 2, "left_coverage": 1.0, "right_coverage": 1.0},
    )
    assert query.lookup(filled, "orders", project=pid)["precedents"] == []


def test_nothing_measured_means_no_precedent_to_offer(filled, pid):
    """Code never proposes a pair of its own. With no measurement in the project
    there is no judgment to replay, and three shelves a prefilter could build
    from names and roles were all measured failing (see `_precedents`)."""
    assert query.lookup(filled, "returns", project=pid)["precedents"] == []


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

    (pair,) = query.lookup(filled, "orders", project=pid)["overlaps"][0]["pairs"]
    assert not {"project", "left_fingerprint", "right_fingerprint"} & set(pair)


# --- asking about a column --------------------------------------------------


def test_a_column_says_which_column_it_came_from_and_which_step_explains_it(filled, pid):
    answer = query.lookup(filled, "stg_orders", "customer_id", project=pid)
    assert answer["derives_from"] == [
        {
            "kind": "Source",
            "table": "orders",
            "path": "data/orders.csv",
            "column": "customer_id",
            "via": "normalize",
            "step": "stg_orders#cleaned",
        }
    ]


def test_lineage_walks_through_a_model_to_the_file_underneath(filled, pid):
    """One hop is what it derives from; `origins` is where it bottoms out."""
    answer = query.lookup(filled, "mart_orders", "amount", project=pid)
    assert [r["table"] for r in answer["derives_from"]] == ["stg_orders"]
    assert answer["origins"] == [
        {"kind": "Source", "table": "orders", "path": "data/orders.csv", "column": "amount"}
    ]


def test_a_shared_key_shows_both_origins(filled, pid):
    """`coalesce(l.k, r.k)` — two edges, each with the step explaining its side."""
    edges = query.lookup(filled, "mart_orders", "customer_id", project=pid)["derives_from"]
    assert {(r["table"], r["via"]) for r in edges} == {
        ("stg_orders", "join"),
        ("customers", "join"),
    }
    origins = query.lookup(filled, "mart_orders", "customer_id", project=pid)["origins"]
    assert {r["table"] for r in origins} == {
        "orders",
        "customers",
    }


def test_a_source_column_says_what_is_built_from_it(filled, pid):
    """The forward direction — what §4.5 needs to answer 'this file changed, so what'."""
    feeds = query.lookup(filled, "orders", "amount", project=pid)["feeds"]
    assert [(r["table"], r["column"]) for r in feeds] == [("stg_orders", "amount")]


def test_a_column_carries_the_catalogs_facts_and_not_a_new_measurement(filled, pid):
    column = query.lookup(filled, "orders", "amount", project=pid)["column"]
    assert column["n_distinct"] == 2
    assert column["role"] is None


# --- misses -----------------------------------------------------------------


def test_an_unknown_table_names_what_there_is(filled, pid):
    with pytest.raises(ValueError, match="mart_orders"):
        query.lookup(filled, "nope", project=pid)


def test_an_unknown_column_names_the_columns_there_are(filled, pid):
    with pytest.raises(ValueError, match="amount"):
        query.lookup(filled, "orders", "nope", project=pid)


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

    columns = query.subgraph(filled, columns=True, project=pid)
    assert "Column" in {n["kind"] for n in columns["nodes"]}
    assert len(columns["nodes"]) > len(tables["nodes"])


def test_the_cap_takes_columns_and_never_the_tables_they_hang_from(filled, pid, monkeypatch):
    """A warehouse of 39 tables drew six hundred columns attached to nothing.

    The nodes were ordered by kind, `Column` sorts first, and the cap kept the
    columns and cut the tables; every `HAS_COLUMN` edge then pointed at a node
    the picture did not have and was dropped. Tables and groups come first now,
    and what the cap takes is columns, whole tables at a time.
    """
    from portia.knowledge.schema import HAS_COLUMN

    tables = query.subgraph(filled, project=pid)["nodes"]
    monkeypatch.setattr(query, "MAX_GRAPH_NODES", len(tables) + 2)

    picture = query.subgraph(filled, columns=True, project=pid)
    assert {n["id"] for n in tables} <= {n["id"] for n in picture["nodes"]}
    columns = [n for n in picture["nodes"] if n["kind"] == "Column"]
    assert len(columns) == 2, "the two slots left over the tables go to columns"
    assert picture["truncated"] and picture["omitted"] > 0

    attached = {e["to"] for e in picture["edges"] if e["kind"] == HAS_COLUMN}
    assert {n["id"] for n in columns} <= attached, "every drawn column hangs from its table"
    assert len({n["properties"]["table"] for n in columns}) == 1, "cut by table, not scattered"


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
    assert counted["derives_from"] == []
    assert "nothing readable underneath it" in query.render_text(counted)

    # The column beside it resolved, and says nothing about derivation at all.
    carried = query.lookup(neo4j_session, "agg_orders", "customer_id", project=pid)
    assert carried["column"]["derivation"] is None
    assert carried["origins"] == [
        {"kind": "Source", "table": "orders", "path": "data/orders.csv", "column": "customer_id"}
    ]


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
