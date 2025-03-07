from homu.main import (
    suppress_ignore_block,
    suppress_pings,
    IGNORE_BLOCK_START,
    IGNORE_BLOCK_END,
)


def test_suppress_pings_in_PR_body():
    # This behavior can be verified by pasting the text into a Markdown editor
    # on Github and checking which usernames are auto-linked.

    body = (
        # Should escape:
        "r? @matklad\n"
        "@bors r+\n"
        "@a\n"  # Minimum length
        "@abcdefghijklmnopqrstuvwxyzabcdefghijklm\n"  # Maximum length
        "@user. @user, @user; @user? @user!\n"
        "@user@user\n"  # Only the first is auto-linked
        "@user/@user/@user\n"  # Only the last is auto-linked
        "@@user\n"
        "/@user\n"
        "-@user\n"
        "@user--name\n"  # Auto-linked, despite being an invalid username
        "@user-\n"  # Auto-linked, despite being an invalid username
        "`@user`\n"  # Code block handling is not implemented

        # Shouldn't escape:
        "mail@example.com\n"
        "@abcdefghijklmnopqrstuvwxyzabcdefghijklmo\n"  # Over maximum length
        "text@user\n"
        "@-\n"
        "@-user\n"
        "@user/\n"
        "@user_\n"
        "_@user\n"
    )

    expect = (
        "r? `@matklad`\n"
        "`@bors` r+\n"
        "`@a`\n"
        "`@abcdefghijklmnopqrstuvwxyzabcdefghijklm`\n"
        "`@user`. `@user`, `@user`; `@user`? `@user`!\n"
        "`@user`@user\n"
        "@user/@user/`@user`\n"
        "@`@user`\n"
        "/`@user`\n"
        "-`@user`\n"
        "`@user--name`\n"
        "`@user-`\n"
        "``@user``\n"
        "mail@example.com\n"
        "@abcdefghijklmnopqrstuvwxyzabcdefghijklmo\n"
        "text@user\n"
        "@-\n"
        "@-user\n"
        "@user/\n"
        "@user_\n"
        "_@user\n"
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
