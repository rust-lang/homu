class Repository:
    treeclosed = -1
    treeclosed_src = None
    gh = None
    label = None
    db = None

    def __init__(self, gh, repo_label, db, owner, name, cfg):
        self.gh = gh
        self.github_repo = gh.repository(owner, name)
        self.repo_label = repo_label
        self.db = db
        self.owner = owner
        self.name = name
        self.cfg = cfg
        db.execute(
            'SELECT treeclosed, treeclosed_src FROM repos WHERE repo = ?',
            [repo_label]
        )
        row = db.fetchone()
        if row:
            self.treeclosed = row[0]
            self.treeclosed_src = row[1]
        else:
            self.treeclosed = -1
            self.treeclosed_src = None

    def update_treeclosed(self, value, src):
        self.treeclosed = value
        self.treeclosed_src = src
        self.db.execute(
            'DELETE FROM repos where repo = ?',
            [self.repo_label]
        )
        if value > 0:
            self.db.execute(
                '''
                    INSERT INTO repos (repo, treeclosed, treeclosed_src)
                    VALUES (?, ?, ?)
                ''',
                [self.repo_label, value, src]
            )

    def __lt__(self, other):
        if self.owner == other.owner:
            return self.name < other.name

        return self.owner < other.owner
