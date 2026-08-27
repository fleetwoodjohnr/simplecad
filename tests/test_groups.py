"""Grouping.

Groups are organisational, not geometric, and the tests are written to hold that
line: the bodies inside a group keep their own identity and their own geometry,
and nothing here ever fuses anything.
"""

from __future__ import annotations

import pytest

from simplecad.core.document import Body, Document, Group
from simplecad.core.errors import CadError
from simplecad.core.history import History


@pytest.fixture
def document():
    doc = Document("Grouped")
    for name in ("A", "B", "C", "D"):
        doc.bodies[name] = Body(name=name)
    return doc


class TestGrouping:
    def test_groups_the_named_bodies(self, document):
        group = document.add_group(["A", "B"])
        assert group.members == ["A", "B"]
        assert document.group_of("A") is group

    def test_a_body_belongs_to_one_group_at_a_time(self, document):
        first = document.add_group(["A", "B"], "First")
        document.add_group(["B", "C"], "Second")
        assert first.members == ["A"]
        assert document.group_of("B").name == "Second"

    def test_grouping_nothing_is_refused(self, document):
        with pytest.raises(CadError):
            document.add_group([])
        with pytest.raises(CadError):
            document.add_group(["nonexistent"])

    def test_names_do_not_collide_with_bodies(self, document):
        document.bodies["Group"] = Body(name="Group")
        assert document.add_group(["A", "B"]).name != "Group"

    def test_duplicates_in_the_request_are_ignored(self, document):
        assert document.add_group(["A", "A", "B"]).members == ["A", "B"]


class TestNesting:
    def test_a_group_can_hold_a_group(self, document):
        document.add_group(["A", "B"], "Inner")
        document.add_group(["Inner", "C"], "Outer")
        assert document.expand(["Outer"]) == ["A", "B", "C"]

    def test_the_tree_shows_only_the_top_level(self, document):
        document.add_group(["A", "B"], "Inner")
        document.add_group(["Inner", "C"], "Outer")
        assert document.root_items() == [("group", "Outer"), ("body", "D")]
        assert document.group_members("Outer") == [
            ("group", "Inner"), ("body", "C")
        ]

    def test_a_click_resolves_to_the_outermost_group(self, document):
        document.add_group(["A", "B"], "Inner")
        document.add_group(["Inner", "C"], "Outer")
        assert document.top_group_of("A").name == "Outer"
        assert document.top_group_of("D") is None

    def test_expansion_survives_a_cycle(self, document):
        """A malformed file must not hang the application."""
        document.groups["X"] = Group(name="X", members=["Y", "A"])
        document.groups["Y"] = Group(name="Y", members=["X", "B"])
        assert sorted(document.expand(["X"])) == ["A", "B"]


class TestUngrouping:
    def test_returns_the_members(self, document):
        document.add_group(["A", "B"], "Frame")
        assert document.ungroup("Frame") == ["A", "B"]
        assert "Frame" not in document.groups
        assert document.group_of("A") is None

    def test_an_inner_group_hands_its_members_to_the_outer_one(self, document):
        document.add_group(["A", "B"], "Inner")
        document.add_group(["Inner", "C"], "Outer")
        document.ungroup("Inner")
        assert document.groups["Outer"].members == ["A", "B", "C"]

    def test_the_bodies_are_untouched(self, document):
        document.add_group(["A", "B"], "Frame")
        document.ungroup("Frame")
        assert set(document.bodies) == {"A", "B", "C", "D"}


class TestMembershipUpkeep:
    def test_a_deleted_body_leaves_its_group(self, document):
        document.add_group(["A", "B", "C"], "Frame")
        del document.bodies["B"]
        document.forget_member("B")
        assert document.groups["Frame"].members == ["A", "C"]

    def test_a_group_emptied_by_deletion_disappears(self, document):
        document.add_group(["A", "B"], "Frame")
        for name in ("A", "B"):
            del document.bodies[name]
            document.forget_member(name)
        assert "Frame" not in document.groups

    def test_emptying_an_inner_group_does_not_strand_the_outer_one(self, document):
        document.add_group(["A"], "Inner")
        document.add_group(["Inner", "B"], "Outer")
        del document.bodies["A"]
        document.forget_member("A")
        assert "Inner" not in document.groups
        assert document.groups["Outer"].members == ["B"]


class TestPersistence:
    def test_groups_round_trip(self, document):
        document.add_group(["A", "B"], "Inner")
        document.add_group(["Inner", "C"], "Outer")
        restored = Document.from_dict(document.to_dict())
        assert restored.expand(["Outer"]) == ["A", "B", "C"]
        assert restored.groups["Outer"].members == ["Inner", "C"]

    def test_a_file_from_before_groups_existed_still_loads(self, document):
        data = document.to_dict()
        del data["groups"]
        assert Document.from_dict(data).groups == {}

    def test_visibility_and_expansion_are_remembered(self, document):
        group = document.add_group(["A", "B"], "Frame")
        group.visible = False
        group.expanded = False
        restored = Document.from_dict(document.to_dict())
        assert restored.groups["Frame"].visible is False
        assert restored.groups["Frame"].expanded is False


class TestUndo:
    def test_grouping_can_be_undone(self, document):
        history = History(document)
        history.record("Group")
        document.add_group(["A", "B"], "Frame")
        history.undo()
        assert document.groups == {}

    def test_ungrouping_can_be_undone(self, document):
        document.add_group(["A", "B"], "Frame")
        history = History(document)
        history.record("Ungroup")
        document.ungroup("Frame")
        assert document.groups == {}
        history.undo()
        assert document.groups["Frame"].members == ["A", "B"]


class TestSelectionQueries:
    """What the contextual toolbar asks about a selection."""

    def _model(self, document, names):
        from simplecad.ui.selection import Picked, SelectionModel

        model = SelectionModel(document, None, {})
        model.picks = [
            Picked(body=name, kind="body", shape=object()) for name in names
        ]
        return model

    def test_a_selected_group_counts_as_one_item(self, document):
        document.add_group(["A", "B"], "Frame")
        model = self._model(document, ["A", "B"])
        assert model.items == ["Frame"]
        assert model.groups == ["Frame"]

    def test_loose_bodies_count_separately(self, document):
        model = self._model(document, ["A", "B"])
        assert model.items == ["A", "B"]
        assert model.groups == []

    def test_a_group_plus_a_body_is_two_items(self, document):
        document.add_group(["A", "B"], "Frame")
        model = self._model(document, ["A", "B", "C"])
        assert model.items == ["Frame", "C"]
