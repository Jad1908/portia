"""The left pane's model — a real directory, filtered to what portia reads.

`ui/tree.py` imports no NiceGUI and no engine, so what the pane *contains* is
testable without a browser. What it must get right: the filter (a file appears
because portia knows it, or because the loader can read its format), the shape
(folders only exist when something under them survived), and the identity a click
hands the rest of the app.
"""

from __future__ import annotations

from portia.ui import state, tree

READABLE = (".csv", ".parquet")


def _project(root):
    """A project with the shape a layered one actually has on disk."""
    (root / "data").mkdir()
    (root / "data" / "orders.csv").write_text("a\n1\n")
    (root / "data" / "customers.csv").write_text("a\n1\n")
    (root / "specs" / "staging").mkdir(parents=True)
    (root / "specs" / "staging" / "stg_orders.yaml").write_text("steps: []\n")
    (root / "models" / "staging").mkdir(parents=True)
    (root / "models" / "staging" / "stg_orders.sql").write_text("select 1\n")
    (root / "notes.md").write_text("not portia's\n")
    (root / "analysis.py").write_text("print()\n")
    return root


def test_a_folder_is_drawn_only_when_something_under_it_survived(tmp_path):
    """Otherwise a repo's worth of empty structure is the left pane."""
    _project(tmp_path)
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "readme.md").write_text("hi\n")

    nodes = tree.build(tmp_path, {}, READABLE)

    assert [n.name for n in nodes] == ["data"], "only the folder with readable files"
    assert tree.folders(nodes) == ["data"]


def test_a_file_portia_cannot_read_is_not_in_the_tree(tmp_path):
    """The curation survives the move to a disk walk — as a filter, not a layout."""
    _project(tmp_path)

    names = [n.name for n in tree.build(tmp_path, {}, READABLE)]

    assert "notes.md" not in names and "analysis.py" not in names


def test_a_known_file_appears_whatever_its_suffix(tmp_path):
    """A `.sql` and a `.yaml` are in no loader's format list; portia wrote them."""
    _project(tmp_path)
    known = {
        "specs/staging/stg_orders.yaml": (state.SPEC, "stg_orders.yaml"),
        "models/staging/stg_orders.sql": (state.MODEL, "models/staging/stg_orders.sql"),
    }

    nodes = tree.build(tmp_path, known, READABLE)

    assert [n.name for n in nodes] == ["data", "models", "specs"]
    assert tree.folders(nodes) == ["data", "models", "models/staging", "specs", "specs/staging"]


def test_the_two_same_named_files_a_flat_list_could_not_tell_apart(tmp_path):
    """The whole reason the sections gave way: `stg_orders` is two files in two
    places, and six flat lists showed two rows with one name and no location."""
    _project(tmp_path)
    known = {
        "specs/staging/stg_orders.yaml": (state.SPEC, "stg_orders.yaml"),
        "models/staging/stg_orders.sql": (state.MODEL, "models/staging/stg_orders.sql"),
    }

    nodes = tree.build(tmp_path, known, READABLE)
    by_name = {n.name: n for n in nodes}
    spec = by_name["specs"].children[0].children[0]
    model = by_name["models"].children[0].children[0]

    assert (spec.rel, spec.kind) == ("specs/staging/stg_orders.yaml", state.SPEC)
    assert (model.rel, model.kind) == ("models/staging/stg_orders.sql", state.MODEL)


def test_a_readable_file_with_no_catalog_entry_is_marked_not_hidden(tmp_path):
    """It is in the repo and portia can read it. Hiding it makes a file that is
    already there discoverable only through a dialog."""
    _project(tmp_path)
    known = {"data/orders.csv": (state.SOURCE, "orders")}

    data = tree.build(tmp_path, known, READABLE)[0]

    assert [(n.name, n.kind) for n in data.children] == [
        ("customers.csv", tree.DATA),
        ("orders.csv", state.SOURCE),
    ]


def test_a_click_carries_the_identity_the_panes_use_not_the_path(tmp_path):
    """A source is addressed by its catalog name, a run by its filename. The tree
    walks paths, so `ident` is the bridge back."""
    _project(tmp_path)
    known = {"data/orders.csv": (state.SOURCE, "orders")}

    orders = tree.build(tmp_path, known, READABLE)[0].children[1]

    assert (orders.rel, orders.ident) == ("data/orders.csv", "orders")


def test_hidden_directories_are_never_walked(tmp_path):
    """`.portia/` included — its brief and its turns reach the pane pinned, and
    showing the catalog's own YAML would invite hand-editing it."""
    _project(tmp_path)
    (tmp_path / ".portia" / "sources").mkdir(parents=True)
    (tmp_path / ".portia" / "sources" / "orders.yaml").write_text("source: data/orders.csv\n")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config.csv").write_text("a\n1\n")

    names = [n.name for n in tree.build(tmp_path, {}, READABLE)]

    assert ".portia" not in names and ".git" not in names


def test_folders_sort_before_files_and_nothing_sorts_by_anything_measured(tmp_path):
    """The only ordering in this pane is the one every file browser uses."""
    (tmp_path / "zzz").mkdir()
    (tmp_path / "zzz" / "a.csv").write_text("a\n1\n")
    (tmp_path / "aaa.csv").write_text("a\n1\n")

    nodes = tree.build(tmp_path, {}, READABLE)

    assert [n.name for n in nodes] == ["zzz", "aaa.csv"]


def test_a_symlinked_directory_is_not_followed(tmp_path):
    """A link pointing at an ancestor is a walk that does not terminate."""
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "orders.csv").write_text("a\n1\n")
    (tmp_path / "loop").symlink_to(tmp_path)

    nodes = tree.build(tmp_path, {}, READABLE)

    assert [n.name for n in nodes] == ["data"]


# --- which folders are open -------------------------------------------------


def test_the_top_level_is_open_and_everything_below_it_is_not():
    """A project's own folders are what you want on opening; a walk below them is
    something you asked for."""
    app = state.App()

    assert app.folder_open("data", 0) is True
    assert app.folder_open("specs/staging", 1) is False


def test_shutting_a_top_level_folder_sticks():
    """One set could not say this: closed is an override of the default, and so
    is open, so both have to be recorded."""
    app = state.App()

    app.toggle_folder("data", 0)
    assert app.folder_open("data", 0) is False

    app.toggle_folder("data", 0)
    assert app.folder_open("data", 0) is True


def test_opening_a_nested_folder_sticks_across_a_rebuild():
    """The tree is rebuilt from disk on every render, so the open state is kept
    against the path — the only thing that survives the rebuild."""
    app = state.App()

    app.toggle_folder("specs/staging", 1)

    assert app.folder_open("specs/staging", 1) is True
    assert app.folder_open("specs/other", 1) is False
