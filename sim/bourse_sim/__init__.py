"""bourse_sim: agent-based market simulation and market-making against the
Go matching engine (../cmd/simserver), driven over a line-delimited JSON
subprocess protocol.

Why a subprocess boundary rather than a rewrite: internal/book is the tested,
benchmarked core (property tests, deterministic replay, real latency
numbers). Everything in this package is new -- the simulated market
participants, the pricing dynamics, the market-making strategy, and the
statistical evaluation of it -- and it's written in Python because that's
where this project's analysis work already lives. The wire boundary keeps
the two halves honest: the engine is unmodified by anything built here.
"""
