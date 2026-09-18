"""`python -m portia.fixtures` — (re)generate and keep the mock data."""

from portia.fixtures import write_fixtures

if __name__ == "__main__":
    for p in write_fixtures():
        print(f"wrote {p}")
