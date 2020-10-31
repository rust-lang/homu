from homu.main import (
    suppress_ignore_block,
    suppress_pings,
    IGNORE_BLOCK_START,
    IGNORE_BLOCK_END,
)


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


def test_suppress_ignore_block_in_PR_body():
    body = (
        "Rollup merge\n"
        "{}\n"
        "[Create a similar rollup](https://fake.xyz/?prs=1,2,3)\n"
        "{}"
    )

    body = body.format(IGNORE_BLOCK_START, IGNORE_BLOCK_END)

    expect = "Rollup merge\n"

    assert suppress_ignore_block(body) == expect
