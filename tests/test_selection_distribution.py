"""Statistical distribution tests for super pal selection logic."""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import secrets
import pytest
from unittest.mock import Mock
import discord


def make_member(name, has_role=False, role=None, bot=False):
    m = Mock(spec=discord.Member)
    m.name = name
    m.bot = bot
    m.roles = [role] if has_role and role else []
    return m


def select_eligible(members, role):
    """Mirror the pre-filter logic from super_pal_of_the_week()."""
    non_bots = [m for m in members if not m.bot]
    return [m for m in non_bots if role not in m.roles]


class TestSelectionDistribution:
    """Verify uniform distribution across N selection rounds."""

    def test_uniform_distribution_across_eligible_members(self):
        role = Mock(spec=discord.Role)
        current = make_member("Current", has_role=True, role=role)
        others = [make_member(f"User{i}", role=role) for i in range(9)]
        all_members = [current] + others

        counts = {m.name: 0 for m in others}
        rounds = 10_000
        for _ in range(rounds):
            eligible = select_eligible(all_members, role)
            chosen = secrets.choice(eligible)
            counts[chosen.name] += 1

        # Each of 9 eligible members should be chosen ~11.1% of the time.
        # Allow ±3% absolute tolerance (generous for 10 000 rounds).
        expected = rounds / len(others)
        for name, count in counts.items():
            assert abs(count - expected) / rounds < 0.03, (
                f"{name} selected {count} times (expected ~{expected:.0f})"
            )

    def test_current_super_pal_never_selected(self):
        role = Mock(spec=discord.Role)
        current = make_member("Current", has_role=True, role=role)
        others = [make_member(f"User{i}", role=role) for i in range(4)]

        for _ in range(1_000):
            eligible = select_eligible([current] + others, role)
            chosen = secrets.choice(eligible)
            assert chosen is not current

    def test_bots_never_selected(self):
        role = Mock(spec=discord.Role)
        bot_member = make_member("BotUser", bot=True)
        human = make_member("Human", role=role)

        for _ in range(500):
            eligible = select_eligible([bot_member, human], role)
            assert bot_member not in eligible
            chosen = secrets.choice(eligible)
            assert chosen is human

    def test_single_eligible_member_always_selected(self):
        role = Mock(spec=discord.Role)
        current = make_member("Current", has_role=True, role=role)
        only_eligible = make_member("Only", role=role)

        for _ in range(200):
            eligible = select_eligible([current, only_eligible], role)
            assert len(eligible) == 1
            chosen = secrets.choice(eligible)
            assert chosen is only_eligible

    def test_two_non_role_members_roughly_equal(self):
        """With two eligible members neither should dominate."""
        role = Mock(spec=discord.Role)
        a = make_member("A", role=role)
        b = make_member("B", role=role)
        counts = {"A": 0, "B": 0}
        rounds = 2_000
        for _ in range(rounds):
            eligible = select_eligible([a, b], role)
            chosen = secrets.choice(eligible)
            counts[chosen.name] += 1

        # Each should be ~50%; allow ±5% absolute.
        for name, count in counts.items():
            assert abs(count - rounds / 2) / rounds < 0.05, (
                f"{name} selected {count} times out of {rounds}"
            )

    def test_no_eligible_members_returns_empty(self):
        role = Mock(spec=discord.Role)
        # All members already hold the role
        members = [make_member(f"User{i}", has_role=True, role=role) for i in range(3)]
        eligible = select_eligible(members, role)
        assert eligible == []

    def test_all_members_bots_returns_empty(self):
        role = Mock(spec=discord.Role)
        bots = [make_member(f"Bot{i}", bot=True) for i in range(3)]
        eligible = select_eligible(bots, role)
        assert eligible == []
