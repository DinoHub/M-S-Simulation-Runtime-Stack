from mns_macvo_adaptor.node_stamps import Pacer


def test_one_pair_in_flight_until_answered():
    p = Pacer(max_inflight=1, timeout_s=1.0)
    assert p.ready(0.0)
    p.sent(100, 0.0)
    assert not p.ready(0.1)
    p.answered(100)
    assert p.ready(0.2)


def test_an_answer_clears_older_pairs_too():
    p = Pacer(max_inflight=2, timeout_s=1.0)
    p.sent(100, 0.0)
    p.sent(200, 0.05)
    assert not p.ready(0.1)
    p.answered(200)
    assert p.ready(0.1)


def test_an_unanswered_pair_expires():
    # MAC-VO publishes nothing for a frame it loses track on.
    p = Pacer(max_inflight=1, timeout_s=0.5)
    p.sent(100, 0.0)
    assert not p.ready(0.4)
    assert p.ready(0.6)
