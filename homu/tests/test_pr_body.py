from homu.main import suppress_pings


def test_suppress_pings_in_PR_body():
    body = (
        "r? @matklad\n"         # should escape
        "@bors r+\n"            # shouldn't
        "mail@example.com"      # shouldn't
    )

    expect = (
        "r? `@matklad`\n"
        "`@bors` r+\n"
        "mail@example.com"
    )

    assert suppress_pings(body) == expect
