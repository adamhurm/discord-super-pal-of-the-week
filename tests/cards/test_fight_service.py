from unittest.mock import patch

import aiosqlite
import pytest


@pytest.fixture
async def db(db_mods):
    db_mod, svc_mod, fs_mod, ps_mod = db_mods
    await db_mod.init_db()
    await svc_mod.sync_members(
        [
            {"discord_id": "p1", "display_name": "Alice", "avatar_url": None},
            {"discord_id": "p2", "display_name": "Bob", "avatar_url": None},
        ]
    )
    # Give both players a card to use
    async with aiosqlite.connect(db_mod.DB_PATH) as conn:
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc).isoformat()
        for pid in ("p1", "p2"):
            for mid in ("p1", "p2"):
                for rarity in ("common", "uncommon", "rare", "legendary"):
                    await conn.execute(
                        "INSERT OR IGNORE INTO user_cards "
                        "(owner_id, card_member_id, rarity, quantity, first_acquired_at) "
                        "VALUES (?, ?, ?, 1, ?)",
                        (pid, mid, rarity, now),
                    )
        await conn.commit()
    return db_mod, svc_mod, fs_mod, ps_mod


# ─── calc_damage tests ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "roll,expected_tier",
    [
        (1, "glancing"),  # Vibe Check min_roll=1, roll=1 hits
        (5, "glancing"),
        (10, "glancing"),
        (11, "direct"),
        (16, "direct"),
        (17, "critical"),
        (19, "critical"),
        (20, "nat20"),
    ],
)
def test_vibe_check_never_misses(roll, expected_tier):
    from superpal.cards.fight_service import calc_damage

    damage, tier = calc_damage("vibe_check", 0, roll)
    assert tier == expected_tier
    assert damage > 0


@pytest.mark.parametrize(
    "roll,expected_tier",
    [
        (1, "miss"),
        (5, "miss"),
        (6, "glancing"),
        (10, "glancing"),
        (11, "direct"),
        (16, "direct"),
        (17, "critical"),
        (20, "nat20"),
    ],
)
def test_body_slam_miss_and_hits(roll, expected_tier):
    from superpal.cards.fight_service import calc_damage

    _, tier = calc_damage("body_slam", 0, roll)
    assert tier == expected_tier


@pytest.mark.parametrize(
    "rarity,expected_hp,expected_atk",
    [
        ("common", 80, 0),
        ("uncommon", 100, 5),
        ("rare", 130, 10),
        ("legendary", 170, 20),
    ],
)
def test_rarity_stats(rarity, expected_hp, expected_atk):
    from superpal.cards.fight_service import RARITY_STATS

    stats = RARITY_STATS[rarity]
    assert stats["hp"] == expected_hp
    assert stats["atk_bonus"] == expected_atk


def test_damage_formula_nat20_common():
    from superpal.cards.fight_service import calc_damage

    # Vibe Check (base 15) + 0 atk_bonus, nat 20 = floor(15 * 2.0) = 30
    damage, tier = calc_damage("vibe_check", 0, 20)
    assert damage == 30
    assert tier == "nat20"


def test_damage_formula_critical_legendary():
    from superpal.cards.fight_service import calc_damage

    # Super Bringus Beam (base 35) + 20 atk_bonus, roll 19 = floor(55 * 1.5) = 82
    damage, tier = calc_damage("super_bringus_beam", 20, 19)
    assert damage == 82
    assert tier == "critical"


def test_damage_formula_glancing():
    from superpal.cards.fight_service import calc_damage

    # Body Slam (base 20) + 5 atk_bonus, roll 8 = floor(25 * 0.5) = 12
    damage, tier = calc_damage("body_slam", 5, 8)
    assert damage == 12
    assert tier == "glancing"


def test_super_bringus_beam_misses_on_low_roll():
    from superpal.cards.fight_service import calc_damage

    # min_roll=14, roll=13 → miss
    damage, tier = calc_damage("super_bringus_beam", 0, 13)
    assert damage == 0
    assert tier == "miss"


# ─── Fight lifecycle tests ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_fight(db):
    _, _, fs, _ = db
    fight = await fs.create_fight("p1", "p2", "quick")
    assert fight.id is not None
    assert fight.status == "pending"
    assert fight.mode == "quick"
    assert fight.challenger_id == "p1"
    assert fight.opponent_id == "p2"


@pytest.mark.asyncio
async def test_accept_fight(db):
    _, _, fs, _ = db
    fight = await fs.create_fight("p1", "p2", "quick")
    accepted = await fs.accept_fight(fight.id)
    assert accepted is not None
    assert accepted.status == "lobby"


@pytest.mark.asyncio
async def test_accept_fight_idempotent_fails(db):
    _, _, fs, _ = db
    fight = await fs.create_fight("p1", "p2", "quick")
    await fs.accept_fight(fight.id)
    second = await fs.accept_fight(fight.id)
    assert second is None  # already lobby, not pending


@pytest.mark.asyncio
async def test_set_fight_cards_and_ready(db):
    _, _, fs, _ = db
    fight = await fs.create_fight("p1", "p2", "quick")
    await fs.accept_fight(fight.id)

    ok = await fs.set_fight_cards(
        fight.id, "p1", [{"card_member_id": "p2", "rarity": "common", "slot": 1}]
    )
    assert ok is True

    ok2 = await fs.set_fight_cards(
        fight.id, "p2", [{"card_member_id": "p1", "rarity": "common", "slot": 1}]
    )
    assert ok2 is True

    both_ready, first_turn = await fs.mark_player_ready(fight.id, "p1")
    assert both_ready is False

    both_ready, first_turn = await fs.mark_player_ready(fight.id, "p2")
    assert both_ready is True
    assert first_turn in ("p1", "p2")

    updated = await fs.get_fight(fight.id)
    assert updated.status == "active"
    assert updated.current_turn_player_id in ("p1", "p2")


@pytest.mark.asyncio
async def test_set_fight_cards_rejects_unowned(db):
    _, _, fs, _ = db
    fight = await fs.create_fight("p1", "p2", "quick")
    await fs.accept_fight(fight.id)
    ok = await fs.set_fight_cards(
        fight.id, "p1", [{"card_member_id": "p2", "rarity": "legendary", "slot": 1}]
    )
    # p1 does own a legendary p2 card (seeded in fixture)
    assert ok is True


# ─── process_action tests ─────────────────────────────────────────────────────


async def _setup_active_fight(fs, mode="quick"):
    """Helper: create + accept + set cards + mark both ready → active fight."""
    fight = await fs.create_fight("p1", "p2", mode)
    await fs.accept_fight(fight.id)

    slots_per = 1 if mode == "quick" else 3
    rarities = ["common", "uncommon", "rare"]
    for pid, mid in (("p1", "p2"), ("p2", "p1")):
        cards = [
            {"card_member_id": mid, "rarity": rarities[i], "slot": i + 1} for i in range(slots_per)
        ]
        await fs.set_fight_cards(fight.id, pid, cards)

    await fs.mark_player_ready(fight.id, "p1")
    await fs.mark_player_ready(fight.id, "p2")
    return await fs.get_fight(fight.id)


@pytest.mark.asyncio
async def test_attack_not_your_turn(db):
    _, _, fs, _ = db
    fight = await _setup_active_fight(fs)
    other = "p2" if fight.current_turn_player_id == "p1" else "p1"
    success, err, _ = await fs.process_action(
        fight.id, other, "attack", {"attack_key": "vibe_check"}
    )
    assert success is False
    assert err == "not_your_turn"


@pytest.mark.asyncio
async def test_attack_deals_damage(db):
    _, _, fs, _ = db
    fight = await _setup_active_fight(fs)
    attacker = fight.current_turn_player_id
    with patch("superpal.cards.fight_service.roll_d20", return_value=20):
        success, err, state = await fs.process_action(
            fight.id, attacker, "attack", {"attack_key": "vibe_check"}
        )
    assert success is True
    assert err == ""
    # After a hit, it should be the other player's turn (or fight ended)
    if state["status"] == "active":
        assert state["current_turn_player_id"] != attacker


@pytest.mark.asyncio
async def test_attack_miss_advances_turn(db):
    _, _, fs, _ = db
    fight = await _setup_active_fight(fs)
    attacker = fight.current_turn_player_id
    # roll 5 on body_slam misses (min_roll=6)
    with patch("superpal.cards.fight_service.roll_d20", return_value=5):
        success, _err, state = await fs.process_action(
            fight.id, attacker, "attack", {"attack_key": "body_slam"}
        )
    assert success is True
    assert state["current_turn_player_id"] != attacker


@pytest.mark.asyncio
async def test_run_not_allowed_in_quick(db):
    _, _, fs, _ = db
    fight = await _setup_active_fight(fs, mode="quick")
    attacker = fight.current_turn_player_id
    success, err, _ = await fs.process_action(fight.id, attacker, "run", {})
    assert success is False
    assert err == "run_not_allowed"


@pytest.mark.asyncio
async def test_run_free_escape_ends_fight(db):
    _db_mod, _, fs, _ps = db
    fight = await _setup_active_fight(fs, mode="extended")
    runner = fight.current_turn_player_id
    with patch("superpal.cards.fight_service.roll_d20", return_value=16):
        success, _err, state = await fs.process_action(fight.id, runner, "run", {})
    assert success is True
    assert state["status"] == "completed"
    # runner loses (opponent wins)
    assert state["winner_id"] != runner


@pytest.mark.asyncio
async def test_run_failed_loses_turn(db):
    _, _, fs, _ = db
    fight = await _setup_active_fight(fs, mode="extended")
    runner = fight.current_turn_player_id
    with patch("superpal.cards.fight_service.roll_d20", return_value=5):
        success, _err, state = await fs.process_action(fight.id, runner, "run", {})
    assert success is True
    assert state["status"] == "active"
    assert state["current_turn_player_id"] != runner


@pytest.mark.asyncio
async def test_quick_battle_win_condition(db):
    db_mod, _, fs, _ps = db
    async with aiosqlite.connect(db_mod.DB_PATH) as conn:
        await conn.execute(
            "UPDATE members SET pringle_balance = 100 WHERE discord_id IN ('p1', 'p2')"
        )
        await conn.commit()

    fight = await _setup_active_fight(fs, mode="quick")

    # Force damage to exceed opponent's HP (80 for common + multiple hits)
    with patch("superpal.cards.fight_service.roll_d20", return_value=20):
        state = None
        for _ in range(10):
            current = (await fs.get_fight(fight.id)).current_turn_player_id
            _success, _, state = await fs.process_action(
                fight.id, current, "attack", {"attack_key": "super_bringus_beam"}
            )
            if state["status"] == "completed":
                break

    assert state is not None
    assert state["status"] == "completed"
    assert state["winner_id"] is not None


@pytest.mark.asyncio
async def test_extended_win_requires_all_3_fainted(db):
    db_mod, _, fs, _ps = db
    async with aiosqlite.connect(db_mod.DB_PATH) as conn:
        await conn.execute(
            "UPDATE members SET pringle_balance = 200 WHERE discord_id IN ('p1', 'p2')"
        )
        await conn.commit()

    fight = await _setup_active_fight(fs, mode="extended")

    completed = False
    for _ in range(60):
        current_fight = await fs.get_fight(fight.id)
        if current_fight.status == "completed":
            completed = True
            break

        actor = current_fight.pending_swap_player_id or current_fight.current_turn_player_id
        if not actor:
            break

        if current_fight.pending_swap_player_id:
            # Pick first available non-fainted, non-active card
            cards = await fs.get_fight_cards(fight.id)
            available = [
                c for c in cards if c.player_id == actor and not c.is_active and not c.is_fainted
            ]
            if not available:
                break
            with patch("superpal.cards.fight_service.roll_d20", return_value=20):
                await fs.process_action(fight.id, actor, "swap", {"slot": available[0].slot})
        else:
            with patch("superpal.cards.fight_service.roll_d20", return_value=20):
                await fs.process_action(
                    fight.id, actor, "attack", {"attack_key": "super_bringus_beam"}
                )

    assert completed is True


@pytest.mark.asyncio
async def test_fight_token_flow(db):
    _db_mod, _, fs, _ = db
    fight = await fs.create_fight("p1", "p2", "quick")
    await fs.accept_fight(fight.id)

    url = await fs.create_fight_token(fight.id, "p1", "http://localhost")
    assert f"/fight/{fight.id}/lobby?ft=" in url

    token = url.split("?ft=")[1]
    result = await fs.use_fight_token(token)
    assert result is not None
    f_id, p_id, session_tok = result
    assert f_id == fight.id
    assert p_id == "p1"
    assert len(session_tok) > 0

    # Token is idempotent — second use returns the same session
    result2 = await fs.use_fight_token(token)
    assert result2 is not None
    _, _, session_tok2 = result2
    assert session_tok2 == session_tok

    # Session is valid
    info = await fs.get_fight_session(session_tok)
    assert info is not None
    assert info["fight_id"] == fight.id
    assert info["player_id"] == "p1"


# ─── get_fight_leaderboard tests ────────────────────────────────────────────


async def _insert_completed_fight(
    conn, challenger_id: str, opponent_id: str, winner_id: str, now: str
) -> int:
    cur = await conn.execute(
        "INSERT INTO fights (mode, challenger_id, opponent_id, status, winner_id, "
        "created_at, last_activity_at) VALUES ('quick', ?, ?, 'completed', ?, ?, ?)",
        (challenger_id, opponent_id, winner_id, now, now),
    )
    return cur.lastrowid


@pytest.mark.asyncio
async def test_fight_leaderboard_wins(db):
    db_mod, _svc_mod, fs_mod, _ps_mod = db
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(db_mod.DB_PATH) as conn:
        # p1 wins 2, p2 wins 1
        await _insert_completed_fight(conn, "p1", "p2", "p1", now)
        await _insert_completed_fight(conn, "p1", "p2", "p1", now)
        await _insert_completed_fight(conn, "p2", "p1", "p2", now)
        await conn.commit()

    rows = await fs_mod.get_fight_leaderboard("wins")
    assert rows[0]["discord_id"] == "p1"
    assert rows[0]["total"] == 2
    assert rows[1]["discord_id"] == "p2"
    assert rows[1]["total"] == 1


@pytest.mark.asyncio
async def test_fight_leaderboard_wins_empty(db):
    _db_mod, _svc_mod, fs_mod, _ps_mod = db
    rows = await fs_mod.get_fight_leaderboard("wins")
    assert rows == []


@pytest.mark.asyncio
async def test_fight_leaderboard_fights_played(db):
    db_mod, _svc_mod, fs_mod, _ps_mod = db
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(db_mod.DB_PATH) as conn:
        await _insert_completed_fight(conn, "p1", "p2", "p1", now)
        await _insert_completed_fight(conn, "p1", "p2", "p2", now)
        await _insert_completed_fight(conn, "p2", "p1", "p1", now)
        await _insert_completed_fight(conn, "p1", "p2", "p1", now)
        await conn.commit()

    rows = await fs_mod.get_fight_leaderboard("fights_played")
    totals = {r["discord_id"]: r["total"] for r in rows}
    assert totals["p1"] == 4
    assert totals["p2"] == 4


@pytest.mark.asyncio
async def test_fight_leaderboard_win_rate_minimum_threshold(db):
    db_mod, _svc_mod, fs_mod, _ps_mod = db
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(db_mod.DB_PATH) as conn:
        # Add a third member with only 2 fights (should not appear)
        await conn.execute(
            "INSERT INTO members (discord_id, display_name, avatar_url, is_excluded, synced_at) "
            "VALUES ('p3', 'Carol', NULL, 0, ?)",
            (now,),
        )
        # p1 and p2 each play 4 fights; p3 plays 2 fights (below the 3-fight minimum)
        await _insert_completed_fight(conn, "p1", "p2", "p1", now)
        await _insert_completed_fight(conn, "p1", "p2", "p1", now)
        await _insert_completed_fight(conn, "p2", "p1", "p2", now)
        await _insert_completed_fight(conn, "p1", "p3", "p1", now)
        await _insert_completed_fight(conn, "p3", "p2", "p3", now)
        await conn.commit()

    rows = await fs_mod.get_fight_leaderboard("win_rate")
    discord_ids = [r["discord_id"] for r in rows]
    # p3 has only 2 completed fights — must not appear
    assert "p3" not in discord_ids
    # p1: 3 wins / 4 fights = 0.75; p2: 1 win / 4 fights = 0.25
    assert rows[0]["discord_id"] == "p1"
    assert abs(rows[0]["total"] - 0.75) < 0.01
    assert rows[0]["total_fights"] == 4


@pytest.mark.asyncio
async def test_fight_leaderboard_pringle_balance(db):
    db_mod, _svc_mod, fs_mod, _ps_mod = db
    async with aiosqlite.connect(db_mod.DB_PATH) as conn:
        await conn.execute("UPDATE members SET pringle_balance = 200 WHERE discord_id = 'p2'")
        await conn.execute("UPDATE members SET pringle_balance = 50 WHERE discord_id = 'p1'")
        await conn.commit()

    rows = await fs_mod.get_fight_leaderboard("pringle_balance")
    assert rows[0]["discord_id"] == "p2"
    assert rows[0]["total"] == 200
    assert rows[1]["discord_id"] == "p1"
    assert rows[1]["total"] == 50


@pytest.mark.asyncio
async def test_fight_leaderboard_escapes(db):
    db_mod, _svc_mod, fs_mod, _ps_mod = db
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(db_mod.DB_PATH) as conn:
        # p1 escapes 2 fights; p2 escapes 1 fight
        fid1 = await _insert_completed_fight(conn, "p1", "p2", "p2", now)  # p1 ran, p2 wins
        fid2 = await _insert_completed_fight(conn, "p1", "p2", "p2", now)  # p1 ran again
        fid3 = await _insert_completed_fight(conn, "p2", "p1", "p1", now)  # p2 ran, p1 wins
        for fid, actor in [(fid1, "p1"), (fid2, "p1"), (fid3, "p2")]:
            await conn.execute(
                "INSERT INTO fight_log (fight_id, actor_id, action_type, narrative_text) "
                "VALUES (?, ?, 'run', 'Escape successful')",
                (fid, actor),
            )
        await conn.commit()

    rows = await fs_mod.get_fight_leaderboard("escapes")
    assert rows[0]["discord_id"] == "p1"
    assert rows[0]["total"] == 2
    assert rows[1]["discord_id"] == "p2"
    assert rows[1]["total"] == 1


@pytest.mark.asyncio
async def test_fight_leaderboard_excludes_excluded_members(db):
    db_mod, _svc_mod, fs_mod, _ps_mod = db
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(db_mod.DB_PATH) as conn:
        # p1 wins 2 fights, then gets excluded
        await _insert_completed_fight(conn, "p1", "p2", "p1", now)
        await _insert_completed_fight(conn, "p1", "p2", "p1", now)
        await conn.execute("UPDATE members SET is_excluded = 1 WHERE discord_id = 'p1'")
        await conn.execute("UPDATE members SET pringle_balance = 500 WHERE discord_id = 'p1'")
        await conn.commit()

    wins = await fs_mod.get_fight_leaderboard("wins")
    assert all(r["discord_id"] != "p1" for r in wins), "excluded member appeared in wins"

    balance = await fs_mod.get_fight_leaderboard("pringle_balance")
    assert all(r["discord_id"] != "p1" for r in balance), (
        "excluded member appeared in pringle_balance"
    )


@pytest.mark.asyncio
async def test_get_fight_state_cards_include_avatar_url(db):
    import aiosqlite
    db_mod, _, fs_mod, _ = db

    # Give p1 a card member with a known avatar_url
    async with aiosqlite.connect(db_mod.DB_PATH) as conn:
        await conn.execute(
            "UPDATE members SET avatar_url = ? WHERE discord_id = ?",
            ("/static/avatars/p2.png", "p2"),
        )
        await conn.commit()

    fight = await fs_mod.create_fight("p1", "p2", "quick")
    await fs_mod.accept_fight(fight.id)
    await fs_mod.set_fight_cards(
        fight.id, "p1", [{"card_member_id": "p2", "rarity": "common", "slot": 1}]
    )
    await fs_mod.set_fight_cards(
        fight.id, "p2", [{"card_member_id": "p1", "rarity": "common", "slot": 1}]
    )
    await fs_mod.mark_player_ready(fight.id, "p1")
    await fs_mod.mark_player_ready(fight.id, "p2")

    state = await fs_mod.get_fight_state(fight.id)

    challenger_cards = state["challenger"]["cards"]
    opponent_cards = state["opponent"]["cards"]
    all_cards = challenger_cards + opponent_cards

    assert all("avatar_url" in c for c in all_cards), "Every card must have avatar_url key"

    # The card whose card_member_id is p2 should carry p2's avatar_url
    p2_card = next(c for c in all_cards if c["card_member_id"] == "p2")
    assert p2_card["avatar_url"] == "/static/avatars/p2.png"

    # p1 has no avatar set — should be None
    p1_card = next(c for c in all_cards if c["card_member_id"] == "p1")
    assert p1_card["avatar_url"] is None
