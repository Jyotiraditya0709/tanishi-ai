-- 0002: the goal tree (node CS4). Cycle, depth and owner rules live here so raw SQL cannot break them either.
-- Never edit this file once a real database exists: add a new migration instead (decision 0007).
-- Do not put a semicolon in a comment: migrate() splits this file on them.
--
-- SQLite does not allow WITH inside a trigger, so a trigger cannot walk the tree. goal_ancestors holds
-- every (goal, ancestor) pair, the goal itself included, and the triggers keep it in step with parent_id.
-- A goal's level is its number of goal_ancestors rows (a root is level 1). The tree stops at level 64.

-- Before this migration, goals.py installed these objects at runtime. Drop them so every db ends up the same.
DROP TRIGGER IF EXISTS goals_check_insert;
DROP TRIGGER IF EXISTS goals_tree_insert;
DROP TRIGGER IF EXISTS goals_id_fixed;
DROP TRIGGER IF EXISTS goals_check_parent;
DROP TRIGGER IF EXISTS goals_tree_reparent;
DROP TRIGGER IF EXISTS goals_tree_delete;
DROP TABLE IF EXISTS goal_ancestors;

CREATE TABLE goal_ancestors (
    goal_id TEXT NOT NULL,
    ancestor_id TEXT NOT NULL,
    PRIMARY KEY (goal_id, ancestor_id)
) WITHOUT ROWID;

CREATE INDEX goal_ancestors_ancestor ON goal_ancestors(ancestor_id, goal_id);

-- Goals that exist already (UNION, not UNION ALL, so even a cycle in old data terminates).
INSERT INTO goal_ancestors(goal_id, ancestor_id)
WITH RECURSIVE up(goal_id, ancestor_id) AS (
    SELECT id, id FROM goals
    UNION
    SELECT up.goal_id, g.parent_id FROM up JOIN goals g ON g.id = up.ancestor_id WHERE g.parent_id IS NOT NULL
)
SELECT goal_id, ancestor_id FROM up;

CREATE TRIGGER goals_check_insert BEFORE INSERT ON goals
BEGIN
    -- Exact match only, so 'USER' or ' user' cannot sort among the user's goals (red-team G3).
    SELECT RAISE(ABORT, 'goal owner must be user or tanishi')
        WHERE NEW.owner IS NULL OR NEW.owner NOT IN ('user', 'tanishi');
    SELECT RAISE(ABORT, 'goal cannot be its own parent')
        WHERE NEW.parent_id = NEW.id;
    SELECT RAISE(ABORT, 'goal parent does not exist')
        WHERE NEW.parent_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM goals WHERE id = NEW.parent_id);
    -- A row already pointing at this new id would close a loop the ancestor table never saw.
    SELECT RAISE(ABORT, 'goal id already has children')
        WHERE EXISTS (SELECT 1 FROM goals WHERE parent_id = NEW.id);
    SELECT RAISE(ABORT, 'goal tree is limited to 64 levels')
        WHERE (SELECT COUNT(*) FROM goal_ancestors WHERE goal_id = NEW.parent_id) >= 64;
    -- INSERT OR REPLACE deletes the old row without an UPDATE, so goals_owner_fixed never sees it.
    SELECT RAISE(ABORT, 'goal owner cannot change')
        WHERE EXISTS (SELECT 1 FROM goals WHERE id = NEW.id AND owner IS NOT NEW.owner);
    -- Every check passed. If this is a REPLACE of an existing leaf, its old row goes without firing
    -- goals_tree_delete (recursive triggers are off), so drop its old ancestor rows here.
    -- goals_tree_insert writes the new ones. A plain INSERT of a new id finds nothing to drop.
    DELETE FROM goal_ancestors WHERE goal_id = NEW.id;
END;

CREATE TRIGGER goals_tree_insert AFTER INSERT ON goals
BEGIN
    INSERT INTO goal_ancestors(goal_id, ancestor_id) VALUES (NEW.id, NEW.id);
    INSERT INTO goal_ancestors(goal_id, ancestor_id)
        SELECT NEW.id, ancestor_id FROM goal_ancestors WHERE goal_id = NEW.parent_id;
END;

CREATE TRIGGER goals_id_fixed BEFORE UPDATE OF id ON goals WHEN NEW.id IS NOT OLD.id
BEGIN
    SELECT RAISE(ABORT, 'goal id cannot change');
END;

-- Who owns a goal decides where it sorts, so a goal can never change owner (red-team G2).
CREATE TRIGGER goals_owner_fixed BEFORE UPDATE OF owner ON goals WHEN NEW.owner IS NOT OLD.owner
BEGIN
    SELECT RAISE(ABORT, 'goal owner cannot change');
END;

-- The (goal, goal) row makes a self-parent a cycle too. The deepest goal of the moved subtree lands at
-- level(new parent) + level(deepest) - level(moved goal) + 1, and must stay within 64.
CREATE TRIGGER goals_check_parent BEFORE UPDATE OF parent_id ON goals WHEN NEW.parent_id IS NOT NULL
BEGIN
    SELECT RAISE(ABORT, 'goal parent does not exist')
        WHERE NOT EXISTS (SELECT 1 FROM goals WHERE id = NEW.parent_id);
    SELECT RAISE(ABORT, 'goal parent would make a cycle')
        WHERE EXISTS (SELECT 1 FROM goal_ancestors WHERE goal_id = NEW.parent_id AND ancestor_id = NEW.id);
    SELECT RAISE(ABORT, 'goal tree is limited to 64 levels')
        WHERE (SELECT COUNT(*) FROM goal_ancestors WHERE goal_id = NEW.parent_id)
            + (SELECT MAX(level) FROM (
                SELECT COUNT(*) AS level FROM goal_ancestors
                WHERE goal_id IN (SELECT goal_id FROM goal_ancestors WHERE ancestor_id = NEW.id)
                GROUP BY goal_id))
            - (SELECT COUNT(*) FROM goal_ancestors WHERE goal_id = NEW.id)
            + 1 > 64;
END;

-- Move the subtree: cut it from its old ancestors, then hang it under the new parent's.
CREATE TRIGGER goals_tree_reparent AFTER UPDATE OF parent_id ON goals WHEN NEW.parent_id IS NOT OLD.parent_id
BEGIN
    DELETE FROM goal_ancestors
        WHERE goal_id IN (SELECT goal_id FROM goal_ancestors WHERE ancestor_id = NEW.id)
          AND ancestor_id IN (SELECT ancestor_id FROM goal_ancestors WHERE goal_id = NEW.id AND ancestor_id != NEW.id);
    INSERT INTO goal_ancestors(goal_id, ancestor_id)
        SELECT sub.goal_id, sup.ancestor_id
        FROM goal_ancestors sub, goal_ancestors sup
        WHERE sub.ancestor_id = NEW.id AND sup.goal_id = NEW.parent_id;
END;

CREATE TRIGGER goals_tree_delete AFTER DELETE ON goals
BEGIN
    DELETE FROM goal_ancestors WHERE goal_id = OLD.id OR ancestor_id = OLD.id;
END;
