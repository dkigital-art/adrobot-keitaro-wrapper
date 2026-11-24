"""Пересчёт долей офферов с учётом pin. Сумма active share всегда = 100."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class OfferWeight:
    id: int
    share: int
    pinned: bool
    status: str  # active | removed


class WeightError(ValueError):
    pass


# Активный оффер с 0% Keitaro не принимает как участника ротации — для этого есть REMOVE.
MIN_ACTIVE_SHARE = 1


def validate_pinned_state(offers: list[OfferWeight]) -> None:
    """
    Жёсткая проверка: сумма pinned active не > 100;
    каждому active unpinned должен остаться минимум MIN_ACTIVE_SHARE.
    """
    active = [o for o in offers if o.status == "active"]
    pinned = [o for o in active if o.pinned]
    unpinned = [o for o in active if not o.pinned]
    pinned_sum = sum(max(0, o.share) for o in pinned)

    if any(o.share < MIN_ACTIVE_SHARE for o in pinned):
        raise WeightError(
            f"Закреплённая доля должна быть не меньше {MIN_ACTIVE_SHARE}%. "
            "Чтобы исключить оффер, используйте REMOVE."
        )
    if pinned_sum > 100:
        raise WeightError(
            f"Сумма закреплённых долей ({pinned_sum}%) превышает 100%. "
            "Снимите pin или уменьшите доли."
        )
    if pinned_sum == 100 and unpinned:
        raise WeightError(
            f"Сумма закреплённых долей уже 100%, "
            f"но есть ещё {len(unpinned)} активных оффер(а) без доли. "
            "Снимите pin или уменьшите закреплённые проценты."
        )
    remainder = 100 - pinned_sum
    if unpinned and remainder < MIN_ACTIVE_SHARE * len(unpinned):
        raise WeightError(
            f"Свободно {remainder}% на {len(unpinned)} незакреплённых оффер(а) — "
            f"каждому нужен минимум {MIN_ACTIVE_SHARE}%. "
            "Снимите pin или уменьшите закреплённые доли."
        )
    if pinned and not unpinned and pinned_sum != 100:
        raise WeightError(
            f"Все активные офферы закреплены, сумма долей = {pinned_sum}% "
            "(нужно ровно 100%). Снимите pin у одного из офферов "
            "или выставьте закреплённые доли так, чтобы сумма была 100%."
        )


def redistribute(offers: list[OfferWeight]) -> list[OfferWeight]:
    """
    Пересчитывает share только среди незапиненных active-офферов.
    Pinned active сохраняют свой процент.
    Removed получают share=0.
    Сумма active всегда сходится к 100 (остаток от округления — первому unpinned).
    """
    for offer in offers:
        if offer.status != "active":
            offer.share = 0

    active = [o for o in offers if o.status == "active"]
    if not active:
        return offers

    validate_pinned_state(offers)

    pinned = [o for o in active if o.pinned]
    unpinned = [o for o in active if not o.pinned]
    pinned_sum = sum(max(0, o.share) for o in pinned)
    remainder = 100 - pinned_sum

    if not unpinned:
        return offers

    base = remainder // len(unpinned)
    extra = remainder % len(unpinned)
    for i, o in enumerate(unpinned):
        o.share = base + (1 if i < extra else 0)

    total = sum(o.share for o in active)
    if total != 100:
        unpinned[0].share += 100 - total

    return offers


def set_unpinned_share(
    offers: list[OfferWeight],
    offer_id: int,
    share: int,
) -> list[OfferWeight]:
    """
    Ручная доля для активного незапиненного оффера.
    Остальные unpinned делят остаток; pinned не трогаем.
    """
    _check_share_range(share)

    target = next((o for o in offers if o.id == offer_id), None)
    if target is None:
        raise WeightError("Оффер не найден")
    if target.status != "active":
        raise WeightError("Нельзя задать долю удалённому офферу")
    if target.pinned:
        raise WeightError("Снимите pin, чтобы менять долю вручную, либо задайте долю у pinned отдельно")

    for o in offers:
        if o.status != "active":
            o.share = 0

    active = [o for o in offers if o.status == "active"]
    pinned = [o for o in active if o.pinned]
    others = [o for o in active if not o.pinned and o.id != offer_id]
    pinned_sum = sum(max(0, o.share) for o in pinned)
    available = 100 - pinned_sum
    max_share = available - MIN_ACTIVE_SHARE * len(others)

    if share > max_share:
        reserve = (
            f", ещё {len(others)} незакреплённым нужно минимум {MIN_ACTIVE_SHARE}% каждому"
            if others
            else ""
        )
        raise WeightError(
            f"Доля {share}% слишком велика: закреплено {pinned_sum}%{reserve}. "
            f"Доступно не больше {max_share}%."
        )

    target.share = share
    remainder = available - share

    if not others:
        if share != available:
            raise WeightError(
                f"Единственный незапиненный оффер должен получить {available}% "
                f"(сейчас {share}%)."
            )
        return offers

    base = remainder // len(others)
    extra = remainder % len(others)
    for i, o in enumerate(others):
        o.share = base + (1 if i < extra else 0)

    total = sum(o.share for o in active)
    if total != 100:
        others[0].share += 100 - total

    return offers


def set_pinned_share(
    offers: list[OfferWeight],
    offer_id: int,
    share: int,
) -> list[OfferWeight]:
    """Меняет долю уже закреплённого оффера с валидацией, затем redistribute unpinned."""
    _check_share_range(share)

    target = next((o for o in offers if o.id == offer_id), None)
    if target is None or target.status != "active":
        raise WeightError("Оффер не найден или неактивен")
    if not target.pinned:
        raise WeightError("Оффер не закреплён")

    target.share = share
    validate_pinned_state(offers)
    return redistribute(offers)


def _check_share_range(share: int) -> None:
    if share < MIN_ACTIVE_SHARE or share > 100:
        raise WeightError(
            f"Доля активного оффера должна быть в диапазоне {MIN_ACTIVE_SHARE}–100%. "
            "Чтобы исключить оффер, используйте REMOVE."
        )


def equal_split(count: int) -> list[int]:
    """Равномерное деление 100% на count частей."""
    if count <= 0:
        return []
    base = 100 // count
    extra = 100 % count
    return [base + (1 if i < extra else 0) for i in range(count)]
