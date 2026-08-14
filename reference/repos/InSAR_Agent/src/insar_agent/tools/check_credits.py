from dataclasses import dataclass, field

from hyp3_sdk import HyP3

from ..config import load_accounts


@dataclass
class CreditsResult:
    accounts: list[dict] = field(default_factory=list)
    total_credits: int = 0
    warnings: list[str] = field(default_factory=list)


def check_credits() -> CreditsResult:
    accounts = load_accounts()
    if not accounts:
        return CreditsResult(warnings=['No accounts configured. Please provide ASF/Earthdata credentials.'])

    result_accounts = []
    total = 0
    for i, acc in enumerate(accounts):
        try:
            h = HyP3(username=acc['username'], password=acc['password'])
            creds = h.check_credits()
            result_accounts.append({
                'index': i,
                'username': acc['username'],
                'credits': creds,
            })
            total += creds
        except Exception as e:
            result_accounts.append({
                'index': i,
                'username': acc['username'],
                'credits': 0,
                'error': str(e),
            })

    w = []
    for a in result_accounts:
        if 'error' in a:
            w.append(f'{a["username"]}: {a["error"]}')
    if not result_accounts:
        w.append('No valid accounts found.')

    return CreditsResult(accounts=result_accounts, total_credits=total, warnings=w)
