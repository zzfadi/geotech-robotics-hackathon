"""Verified single-participant departure; never selects End for everyone."""
import re

async def leave_agent_call(page):
    if page is None or page.is_closed():
        return
    # An unfinished native invitation can hide/intercept the call controls.
    close = page.get_by_role('button', name='Close dialog', exact=True)
    if await close.count() == 1 and await close.is_visible():
        await close.click(timeout=5000)
    rejoin = page.get_by_role('button', name=re.compile(r'^(Rejoin|Join again)$'))
    if await rejoin.count() and await rejoin.first.is_visible():
        return
    leave = page.get_by_role('button', name=re.compile(r'^Leave call')).first
    if not await leave.is_visible():
        raise RuntimeError('Cannot verify agent departure: Leave call control is unavailable')
    await leave.click(timeout=10000)
    # Host confirmation must only leave this participant, never end others' call.
    just_leave = page.get_by_role('button', name=re.compile(r'^(Just leave|Leave meeting)$'))
    if await just_leave.count() == 1 and await just_leave.is_visible():
        await just_leave.click(timeout=5000)
    try:
        await rejoin.first.wait_for(state='visible', timeout=15000)
    except Exception as exc:
        raise RuntimeError('Agent departure not confirmed; preserve the call and inspect the dialog') from exc
