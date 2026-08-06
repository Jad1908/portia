"""Reading a `sql` step's columns out of its text — `docs/SQL_LINEAGE.md`.

These are about the parser alone: given a query and what its inputs hold, which
output columns come out and which input columns are underneath each one. Nothing
here builds a graph, and nothing here runs any SQL.

Two things are worth pinning beyond "it works". **`transformed` is structural** —
it is read off the parse tree rather than guessed from names, and it has to
survive a CTE, because a value carried through three sub-selects was still
carried. And **partial answers are refused**: if one origin of a column cannot be
matched to a declared input, the column comes back with none, because half a
lineage in the store is a guess wearing structure's clothes.
"""

from __future__ import annotations

import pytest

from portia.knowledge.sqllineage import LineageUnreadable, Origin, column_origins

INPUTS = {
    "orders": ["order_id", "customer_id", "amount", "country_name"],
    "customers": ["customer_id", "name", "country_code"],
}


def _origins(sql: str, column: str) -> set[tuple[str, str, bool]]:
    found = column_origins(sql, INPUTS)[column]
    return {(o.table, o.column, o.transformed) for o in found}


def test_a_bare_reference_is_carried_and_a_function_changes_it():
    sql = "SELECT order_id, trim(customer_id) AS customer_id FROM orders"
    assert _origins(sql, "order_id") == {("orders", "order_id", False)}
    assert _origins(sql, "customer_id") == {("orders", "customer_id", True)}


def test_a_rename_is_not_a_transform():
    """`transformed` says whether the *values* changed, which a rename does not.

    `build.py` is what turns that plus the names into carried/renamed/changed —
    the parser reports one structural fact and does not rank anything.
    """
    assert _origins("SELECT order_id AS id FROM orders", "id") == {("orders", "order_id", False)}


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT amount FROM orders",
        "WITH c AS (SELECT amount FROM orders) SELECT amount FROM c",
        "WITH c AS (SELECT amount AS a FROM orders) SELECT a AS amount FROM c",
    ],
)
def test_carrying_survives_however_many_hops_it_takes(sql):
    assert _origins(sql, "amount") == {("orders", "amount", False)}


def test_one_function_anywhere_in_the_path_is_enough_to_change_it():
    sql = "WITH c AS (SELECT round(amount) AS amount FROM orders) SELECT amount FROM c"
    assert _origins(sql, "amount") == {("orders", "amount", True)}


def test_a_computed_column_names_every_input_it_used():
    """Several origins is what makes a composite free from the edge count (§4.2)."""
    sql = "SELECT amount * order_id AS weighted FROM orders"
    assert _origins(sql, "weighted") == {
        ("orders", "amount", True),
        ("orders", "order_id", True),
    }


def test_a_case_expression_names_its_branches_without_weighting_them():
    sql = "SELECT CASE WHEN amount > 1 THEN amount ELSE order_id END AS adj FROM orders"
    assert _origins(sql, "adj") == {("orders", "amount", True), ("orders", "order_id", True)}


def test_a_star_is_expanded_from_the_declared_inputs():
    assert list(column_origins("SELECT * FROM orders", INPUTS)) == INPUTS["orders"]


def test_a_join_inside_the_hatch_traces_each_side_to_its_own_table():
    sql = "SELECT o.amount, c.name FROM orders o JOIN customers c ON o.customer_id = c.customer_id"
    assert _origins(sql, "amount") == {("orders", "amount", False)}
    assert _origins(sql, "name") == {("customers", "name", False)}


def test_a_column_with_no_input_column_underneath_it_comes_back_empty():
    """Not an error — a real answer, and `build.py` marks the node with it."""
    sql = "SELECT customer_id, count(*) AS n, 1 AS lit FROM orders GROUP BY 1"
    origins = column_origins(sql, INPUTS)
    assert origins["n"] == []
    assert origins["lit"] == []
    assert origins["customer_id"] == [Origin("orders", "customer_id", False)]


def test_unparseable_and_unresolvable_are_told_apart():
    """Two different next moves, so two reasons rather than one catch-all."""
    with pytest.raises(LineageUnreadable, match="could not be parsed"):
        column_origins("SELECT FROM WHERE", INPUTS)
    with pytest.raises(LineageUnreadable, match="could not be resolved"):
        column_origins("SELECT nope FROM orders", INPUTS)


def test_a_star_over_a_table_the_schema_does_not_hold_is_refused():
    """It expands to a column literally called `*`, which is worse than a refusal."""
    with pytest.raises(LineageUnreadable, match=r"\*"):
        column_origins("SELECT * FROM mystery", INPUTS)


def test_a_long_parser_complaint_is_cut_to_something_readable():
    long = "SELECT " + ", ".join(f"'{i}' AS c{i}" for i in range(200)) + " FROM orders WHERE"
    with pytest.raises(LineageUnreadable) as raised:
        column_origins(long, INPUTS)
    # It ends up in a build report and a CLI line, beside twenty others.
    assert len(str(raised.value)) < 300
    assert "\n" not in str(raised.value)
