"""Contract-surface probes for a project's deployed testnet contracts.

Most of these projects have no published address book. That absence is itself the
finding -- it is what stops an integrator (or a QA bot) from testing them, and it
is the most useful single line in a pre-launch report.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .rpc import SEL, Rpc, RpcError, dec_address, dec_string, dec_uint

# EIP-1967 standard slots
IMPL_SLOT = "0x360894a13ba1a3210667c828492db98dca3e2076cc3735a920a3ca505d382bbc"
BEACON_SLOT = "0xa3f0ad74e5423aebfd80d3ef4346578335a9a72aeaee59ff6cb3582b35133d50"


@dataclass
class ContractReport:
    project: str
    address: str | None
    label: str = ""
    deployed: bool = False
    code_bytes: int = 0
    is_proxy: bool = False
    implementation: str | None = None
    token: dict = field(default_factory=dict)
    paused: bool | None = None
    owner: str | None = None
    calls: dict = field(default_factory=dict)
    verdict: str = "unknown"
    score: float = 0.0

    def to_dict(self) -> dict:
        return {
            "project": self.project,
            "address": self.address,
            "label": self.label,
            "deployed": self.deployed,
            "code_bytes": self.code_bytes,
            "is_proxy": self.is_proxy,
            "implementation": self.implementation,
            "token": self.token,
            "paused": self.paused,
            "owner": self.owner,
            "verdict": self.verdict,
            "score": round(self.score, 1),
        }


def probe_contract(rpc: Rpc, project: str, address: str | None, label: str = "") -> ContractReport:
    rep = ContractReport(project=project, address=address, label=label)

    if not address:
        rep.verdict = "address unknown"
        rep.score = 0.0
        return rep

    try:
        code = rpc.code(address)
    except RpcError:
        rep.verdict = "rpc error"
        return rep

    if len(code) <= 2:
        rep.verdict = "no code at address"
        return rep

    rep.deployed = True
    rep.code_bytes = (len(code) - 2) // 2
    rep.verdict = "deployed"
    rep.score = 60.0

    # proxy detection
    try:
        impl_raw = rpc.storage(address, IMPL_SLOT)
        impl = dec_address(impl_raw)
        if impl:
            rep.is_proxy = True
            rep.implementation = impl
            beacon = dec_address(rpc.storage(address, BEACON_SLOT))
            if beacon:
                rep.implementation = f"beacon:{beacon}"
    except RpcError:
        pass

    # view-call surface
    for fn, sel in SEL.items():
        if fn == "implementation":
            continue
        raw = rpc.eth_call(address, sel)
        if raw and raw not in ("0x", ""):
            rep.calls[fn] = True
            if fn == "name":
                rep.token["name"] = dec_string(raw)
            elif fn == "symbol":
                rep.token["symbol"] = dec_string(raw)
            elif fn == "decimals":
                rep.token["decimals"] = dec_uint(raw)
            elif fn == "totalSupply":
                rep.token["totalSupply"] = dec_uint(raw)
            elif fn == "paused":
                rep.paused = bool(dec_uint(raw))
            elif fn == "owner":
                rep.owner = dec_address(raw)
        else:
            rep.calls[fn] = False

    if rep.token.get("name") or rep.token.get("symbol"):
        rep.verdict = "token"
        rep.score += 20
    elif rep.is_proxy:
        rep.verdict = "proxy"
        rep.score += 25
    elif sum(rep.calls.values()):
        rep.verdict = "contract (partial ABI match)"
        rep.score += 15
    else:
        rep.verdict = "contract (no standard view interface)"
        rep.score += 5

    if rep.paused:
        rep.verdict += " -- PAUSED"
        rep.score -= 20
    return rep
