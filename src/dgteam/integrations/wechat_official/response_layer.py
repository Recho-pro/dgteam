from __future__ import annotations

import re
import unicodedata

from dgteam.integrations.wechat_official.formatter import (
    format_ambiguous_result,
    format_market_snapshot,
    format_no_result,
)
from dgteam.integrations.wechat_official.models import (
    WechatOfficialImageCandidateResolution,
    WechatOfficialMarketReplyPlan,
)
from dgteam.integrations.wechat_official.response_image_resolution import (
    resolve_image_candidate_queries,
)
from dgteam.integrations.wechat_official.response_snapshot_plans import (
    build_backend_refinement_plan,
    lookup_snapshot_for_candidate,
    strip_auto_refinement_prefix,
)
from dgteam.query_api.service import QueryService

PURE_STORAGE_CAPACITY_VALUES = {64, 128, 256, 512, 1024, 2048}
class WechatOfficialMarketResponseLayer:
    def __init__(self, *, query_service: QueryService):
        self.query = query_service

    def resolve_query(
        self,
        query: str,
        *,
        limit: int = 6,
        preferred_brand: str = "",
        preferred_family: str = "",
    ) -> WechatOfficialMarketReplyPlan:
        search_payload = self.query.search(query, limit=limit)
        results = list(search_payload.get("results") or [])
        results = self._apply_preferences(
            query=query,
            results=results,
            preferred_brand=str(preferred_brand or "").strip(),
            preferred_family=str(preferred_family or "").strip(),
        )
        if not results:
            return WechatOfficialMarketReplyPlan(
                kind="no_result",
                query=query,
                reply_text=format_no_result(query),
            )
        if self._can_auto_snapshot_from_family_cluster(results):
            candidate = dict(results[0] or {})
            snapshot = self.snapshot_for_candidate(candidate)
            if snapshot.get("ok"):
                refined_plan = self._try_contextual_snapshot_refinement(
                    query=query,
                    candidate=candidate,
                    snapshot=snapshot,
                    preferred_brand=preferred_brand,
                    preferred_family=preferred_family,
                )
                if refined_plan.kind != "empty":
                    return refined_plan
                return WechatOfficialMarketReplyPlan(
                    kind="snapshot",
                    query=query,
                    candidate=candidate,
                    snapshot=snapshot,
                    reply_text=format_market_snapshot(candidate=candidate, snapshot=snapshot),
                )
        top_candidate = dict(results[0] or {})
        top_snapshot: dict[str, object] = {}
        if self._should_try_top_candidate_snapshot(
            query=query,
            results=results,
            preferred_brand=preferred_brand,
            preferred_family=preferred_family,
        ):
            top_snapshot = self.snapshot_for_candidate(top_candidate)
            if top_snapshot.get("ok"):
                refined_plan = self._try_contextual_snapshot_refinement(
                    query=query,
                    candidate=top_candidate,
                    snapshot=top_snapshot,
                    preferred_brand=preferred_brand,
                    preferred_family=preferred_family,
                )
                if refined_plan.kind != "empty":
                    return refined_plan
        if self._is_ambiguous_query(query, results):
            return WechatOfficialMarketReplyPlan(
                kind="ambiguous",
                query=query,
                results=[dict(item or {}) for item in results[:limit]],
                reply_text=format_ambiguous_result(query, results),
            )

        candidate = top_candidate
        snapshot = top_snapshot if top_snapshot.get("ok") else self.snapshot_for_candidate(candidate)
        if not snapshot.get("ok"):
            return WechatOfficialMarketReplyPlan(
                kind="ambiguous",
                query=query,
                results=[dict(item or {}) for item in results[:limit]],
                reply_text=format_ambiguous_result(query, results),
            )
        refined_plan = self._try_contextual_snapshot_refinement(
            query=query,
            candidate=candidate,
            snapshot=snapshot,
            preferred_brand=preferred_brand,
            preferred_family=preferred_family,
        )
        if refined_plan.kind != "empty":
            return refined_plan

        return WechatOfficialMarketReplyPlan(
            kind="snapshot",
            query=query,
            candidate=candidate,
            snapshot=snapshot,
            reply_text=format_market_snapshot(candidate=candidate, snapshot=snapshot),
        )

    def resolve_candidate(self, candidate: dict[str, object]) -> WechatOfficialMarketReplyPlan:
        candidate_payload = dict(candidate or {})
        snapshot = self.snapshot_for_candidate(candidate_payload)
        if not snapshot.get("ok"):
            return WechatOfficialMarketReplyPlan(kind="empty")
        return WechatOfficialMarketReplyPlan(
            kind="snapshot",
            candidate=candidate_payload,
            snapshot=snapshot,
            reply_text=format_market_snapshot(candidate=candidate_payload, snapshot=snapshot),
        )

    def resolve_refinement_query(
        self,
        *,
        base_candidate: dict[str, object],
        refinement_query: str,
    ) -> WechatOfficialMarketReplyPlan:
        candidate_payload = dict(base_candidate or {})
        if not candidate_payload:
            return WechatOfficialMarketReplyPlan(kind="empty")

        return build_backend_refinement_plan(
            query_service=self.query,
            candidate=candidate_payload,
            refinement_query=refinement_query,
        )

    def resolve_image_candidates(
        self,
        *,
        recognized_summary: str,
        candidate_queries: list[str],
        preferred_brand: str = "",
        preferred_family: str = "",
        query_limit: int = 3,
        max_queries: int = 4,
    ) -> WechatOfficialImageCandidateResolution:
        return resolve_image_candidate_queries(
            self,
            recognized_summary=recognized_summary,
            candidate_queries=candidate_queries,
            preferred_brand=preferred_brand,
            preferred_family=preferred_family,
            query_limit=query_limit,
            max_queries=max_queries,
        )

    def snapshot_for_candidate(self, candidate: dict[str, object]) -> dict[str, object]:
        return lookup_snapshot_for_candidate(self.query, candidate)

    def _try_contextual_snapshot_refinement(
        self,
        *,
        query: str,
        candidate: dict[str, object],
        snapshot: dict[str, object],
        preferred_brand: str,
        preferred_family: str,
    ) -> WechatOfficialMarketReplyPlan:
        refinement_query = self._build_contextual_refinement_query(
            query=query,
            candidate=candidate,
            preferred_brand=preferred_brand,
            preferred_family=preferred_family,
        )
        if not refinement_query:
            return WechatOfficialMarketReplyPlan(kind="empty")

        return build_backend_refinement_plan(
            query_service=self.query,
            candidate=candidate,
            refinement_query=refinement_query,
            strip_auto_prefix=True,
        )

    @classmethod
    def _should_try_top_candidate_snapshot(
        cls,
        *,
        query: str,
        results: list[dict[str, object]],
        preferred_brand: str,
        preferred_family: str,
    ) -> bool:
        if not results:
            return False
        top_candidate = dict(results[0] or {})
        if not cls._build_contextual_refinement_query(
            query=query,
            candidate=top_candidate,
            preferred_brand=preferred_brand,
            preferred_family=preferred_family,
        ):
            return False
        if len(results) == 1:
            return True
        if len(results) <= 2:
            return True
        top_score = cls._candidate_score(
            top_candidate,
            query=query,
            preferred_brand=preferred_brand,
            preferred_family=preferred_family,
        )
        second_score = cls._candidate_score(
            dict(results[1] or {}),
            query=query,
            preferred_brand=preferred_brand,
            preferred_family=preferred_family,
        )
        if top_score - second_score >= 12:
            return True

        top_engine_score = cls._raw_search_score(top_candidate)
        second_engine_score = cls._raw_search_score(dict(results[1] or {}))
        if top_engine_score > 0 and second_engine_score <= 0:
            return True
        if top_engine_score - second_engine_score >= 1200:
            return True
        if second_engine_score > 0 and top_engine_score >= second_engine_score * 1.35:
            return True
        return False

    @staticmethod
    def _raw_search_score(item: dict[str, object]) -> float:
        try:
            return float(item.get("score") or 0)
        except (TypeError, ValueError):
            return 0.0

    @classmethod
    def _build_contextual_refinement_query(
        cls,
        *,
        query: str,
        candidate: dict[str, object],
        preferred_brand: str,
        preferred_family: str,
    ) -> str:
        capacity_hints = sorted(cls._extract_capacity_hints(query))
        keyword_hints = cls._contextual_keyword_hints(
            query=query,
            candidate=candidate,
            preferred_brand=preferred_brand,
            preferred_family=preferred_family,
        )
        if not capacity_hints and not keyword_hints:
            return ""
        return " ".join([*capacity_hints, *keyword_hints]).strip()

    @classmethod
    def _contextual_keyword_hints(
        cls,
        *,
        query: str,
        candidate: dict[str, object],
        preferred_brand: str,
        preferred_family: str,
    ) -> list[str]:
        context_surfaces = cls._context_surfaces(
            candidate=candidate,
            preferred_brand=preferred_brand,
            preferred_family=preferred_family,
        )
        family_tokens: set[str] = set()
        family_surfaces: set[str] = set()
        for surface in context_surfaces:
            compact = cls._compact_surface(surface)
            if compact:
                family_surfaces.add(compact)
            family_tokens.update(cls._surface_aliases(surface))

        brand_aliases = cls._brand_aliases(str(preferred_brand or candidate.get("brand_title") or ""))
        hints: list[str] = []
        seen: set[str] = set()
        for hint in cls._extract_keyword_hints(query):
            if not hint or hint in seen:
                continue
            seen.add(hint)
            if cls._is_family_related_hint(hint, family_tokens=family_tokens, family_surfaces=family_surfaces):
                continue
            if any(hint == alias or hint in alias or alias in hint for alias in brand_aliases):
                continue
            if not cls._looks_like_spec_hint(hint):
                continue
            hints.append(hint)
        return hints

    @staticmethod
    def _context_surfaces(
        *,
        candidate: dict[str, object],
        preferred_brand: str,
        preferred_family: str,
    ) -> list[str]:
        return [
            str(candidate.get("brand_title") or ""),
            str(candidate.get("series_title") or ""),
            str(candidate.get("family_title") or ""),
            str(candidate.get("model_title") or ""),
            str(candidate.get("label") or ""),
            str(preferred_brand or ""),
            str(preferred_family or ""),
        ]

    @classmethod
    def _is_family_related_hint(
        cls,
        hint: str,
        *,
        family_tokens: set[str],
        family_surfaces: set[str],
    ) -> bool:
        if not hint:
            return True
        if hint in family_tokens:
            return True
        if any(hint == surface or hint in surface or surface in hint for surface in family_surfaces if surface):
            return True
        series_alias = re.fullmatch(r"s(\d{1,2})", hint)
        if series_alias:
            series_number = series_alias.group(1)
            if any(
                f"series{series_number}" in surface or f"s{series_number}" in surface
                for surface in family_surfaces
            ):
                return True
        return False

    @classmethod
    def _surface_aliases(cls, surface: str) -> set[str]:
        compact = cls._compact_surface(surface)
        if not compact:
            return set()
        aliases = set(cls._surface_tokens(compact))
        aliases.add(compact)
        series_alias = re.search(r"series(\d{1,2})", compact)
        if series_alias:
            aliases.add(f"s{series_alias.group(1)}")
        return aliases

    @staticmethod
    def _is_ambiguous_query(query: str, results: list[dict[str, object]]) -> bool:
        labels = [
            str(item.get("label") or item.get("family_title") or item.get("model_title") or "").strip()
            for item in results
        ]
        normalized = {WechatOfficialMarketResponseLayer._compact_surface(label) for label in labels if label}
        query_compact = WechatOfficialMarketResponseLayer._compact_surface(query)
        if not query_compact:
            return True
        if query_compact in normalized:
            return False
        return len(normalized) > 1

    @staticmethod
    def _compact_surface(text: str) -> str:
        normalized = unicodedata.normalize("NFKC", str(text or "").strip()).lower()
        return "".join(ch for ch in normalized if ch.isalnum() or "\u4e00" <= ch <= "\u9fff")

    @classmethod
    def _can_auto_snapshot_from_family_cluster(cls, results: list[dict[str, object]]) -> bool:
        if not results:
            return False
        families = {
            cls._compact_surface(str(item.get("family_title") or item.get("model_title") or item.get("label") or ""))
            for item in results[:6]
        }
        families = {item for item in families if item}
        return len(families) == 1

    @classmethod
    def _apply_preferences(
        cls,
        query: str,
        results: list[dict[str, object]],
        *,
        preferred_brand: str,
        preferred_family: str,
    ) -> list[dict[str, object]]:
        if not results:
            return []

        filtered = list(results)
        brand_matches = cls._filter_brand_matches(filtered, preferred_brand)
        if brand_matches:
            filtered = brand_matches

        family_matches = cls._filter_family_matches(
            filtered,
            preferred_family,
            preferred_brand=preferred_brand,
        )
        if family_matches:
            filtered = family_matches

        ranked = cls._rank_results(
            filtered,
            query=query,
            preferred_brand=preferred_brand,
            preferred_family=preferred_family,
        )
        return ranked

    @classmethod
    def _filter_brand_matches(cls, results: list[dict[str, object]], preferred_brand: str) -> list[dict[str, object]]:
        aliases = cls._brand_aliases(preferred_brand)
        if not aliases:
            return []
        matched: list[dict[str, object]] = []
        for item in results:
            brand_surface = cls._compact_surface(str(item.get("brand_title") or ""))
            if not brand_surface:
                continue
            if any(alias in brand_surface or brand_surface in alias for alias in aliases):
                matched.append(item)
        return matched

    @classmethod
    def _filter_family_matches(
        cls,
        results: list[dict[str, object]],
        preferred_family: str,
        *,
        preferred_brand: str = "",
    ) -> list[dict[str, object]]:
        family_surface = cls._compact_surface(preferred_family)
        if not family_surface:
            return []
        brand_aliases = cls._brand_aliases(preferred_brand)
        reduced_family_surface = cls._strip_known_brand_aliases(family_surface, brand_aliases)

        matched: list[dict[str, object]] = []
        for item in results:
            candidate_family = cls._compact_surface(
                str(item.get("family_title") or item.get("model_title") or item.get("label") or "")
            )
            if not candidate_family:
                continue
            reduced_candidate_family = cls._strip_known_brand_aliases(candidate_family, brand_aliases)
            if (
                family_surface in candidate_family
                or candidate_family in family_surface
                or (reduced_family_surface and reduced_family_surface in reduced_candidate_family)
                or (reduced_family_surface and reduced_candidate_family in reduced_family_surface)
            ):
                matched.append(item)
        return matched

    @classmethod
    def _brand_aliases(cls, preferred_brand: str) -> set[str]:
        compact = cls._compact_surface(preferred_brand)
        if not compact:
            return set()

        alias_groups = {
            cls._compact_surface("苹果"): {"苹果", "apple", "iphone"},
            cls._compact_surface("华为"): {"华为", "华为智选手机", "huawei"},
            cls._compact_surface("联想"): {"联想", "联想电脑", "lenovo"},
            cls._compact_surface("荣耀"): {"荣耀", "honor"},
            cls._compact_surface("华硕"): {"华硕", "华硕电脑", "asus"},
            cls._compact_surface("戴尔"): {"戴尔", "戴尔电脑", "dell"},
            cls._compact_surface("机械革命"): {"机械革命", "mechrevo"},
            cls._compact_surface("红米"): {"红米", "redmi"},
            cls._compact_surface("小米"): {"小米", "xiaomi"},
            cls._compact_surface("vivo"): {"vivo"},
            cls._compact_surface("iqoo"): {"iqoo", "iqoo手机"},
            cls._compact_surface("oppo"): {"oppo"},
            cls._compact_surface("一加"): {"一加", "oneplus"},
        }
        for key, aliases in alias_groups.items():
            alias_surfaces = {cls._compact_surface(item) for item in aliases if cls._compact_surface(item)}
            if (
                compact == key
                or compact in alias_surfaces
                or key in compact
                or any(alias in compact or compact in alias for alias in alias_surfaces)
            ):
                return alias_surfaces
        return {compact}

    @classmethod
    def _strip_known_brand_aliases(cls, surface: str, aliases: set[str]) -> str:
        compact = cls._compact_surface(surface)
        if not compact or not aliases:
            return compact
        for alias in sorted(aliases, key=len, reverse=True):
            if alias and compact.startswith(alias):
                compact = compact[len(alias) :]
                break
        return compact

    @classmethod
    def _rank_results(
        cls,
        results: list[dict[str, object]],
        *,
        query: str,
        preferred_brand: str,
        preferred_family: str,
    ) -> list[dict[str, object]]:
        if not results:
            return []
        scored: list[tuple[int, int, dict[str, object]]] = []
        for index, item in enumerate(results):
            scored.append(
                (
                    cls._candidate_score(
                        item,
                        query=query,
                        preferred_brand=preferred_brand,
                        preferred_family=preferred_family,
                    ),
                    -index,
                    item,
                )
            )
        scored.sort(key=lambda entry: (entry[0], entry[1]), reverse=True)
        return [dict(item or {}) for _score, _index, item in scored]

    @classmethod
    def _candidate_score(
        cls,
        item: dict[str, object],
        *,
        query: str,
        preferred_brand: str,
        preferred_family: str,
    ) -> int:
        score = 0
        query_surface = cls._compact_surface(query)
        family_surface = cls._compact_surface(
            str(item.get("family_title") or item.get("model_title") or item.get("label") or "")
        )
        brand_surface = cls._compact_surface(str(item.get("brand_title") or ""))

        if query_surface and family_surface:
            if query_surface == family_surface:
                score += 260
            elif query_surface in family_surface or family_surface in query_surface:
                score += 180
            score += cls._surface_overlap_score(query_surface, family_surface)

        brand_aliases = cls._brand_aliases(preferred_brand)
        if preferred_brand:
            if brand_surface and any(alias in brand_surface or brand_surface in alias for alias in brand_aliases):
                score += 220
            else:
                score -= 120

        preferred_family_surface = cls._compact_surface(preferred_family)
        reduced_preferred_family = cls._strip_known_brand_aliases(preferred_family_surface, brand_aliases)
        reduced_family_surface = cls._strip_known_brand_aliases(family_surface, brand_aliases)
        if preferred_family_surface and family_surface:
            if preferred_family_surface == family_surface:
                score += 200
            elif preferred_family_surface in family_surface or family_surface in preferred_family_surface:
                score += 140
            elif reduced_preferred_family and reduced_family_surface:
                if reduced_preferred_family == reduced_family_surface:
                    score += 160
                elif (
                    reduced_preferred_family in reduced_family_surface
                    or reduced_family_surface in reduced_preferred_family
                ):
                    score += 110
                score += cls._surface_overlap_score(reduced_preferred_family, reduced_family_surface)
        score += cls._family_spec_alignment_score(query_surface=query_surface, family_surface=family_surface)
        return score

    @classmethod
    def _family_spec_alignment_score(cls, *, query_surface: str, family_surface: str) -> int:
        if not query_surface or not family_surface:
            return 0
        score = 0
        query_has_cellular = any(token in query_surface for token in ("蜂窝", "cellular", "esim"))
        family_has_cellular = any(token in family_surface for token in ("蜂窝", "cellular", "esim"))
        if query_has_cellular == family_has_cellular:
            if query_has_cellular:
                score += 40
        elif family_has_cellular:
            score -= 60
        else:
            score -= 20

        if "gps" in query_surface:
            if "gps" in family_surface:
                score += 24
            else:
                score -= 30

        for token in ("降噪", "标准款", "p93", "p63", "20w", "30w", "45w", "65w"):
            if token in query_surface:
                if token in family_surface:
                    score += 26
                else:
                    score -= 18
        return score

    @classmethod
    def _surface_overlap_score(cls, left: str, right: str) -> int:
        left_tokens = cls._surface_tokens(left)
        right_tokens = cls._surface_tokens(right)
        if not left_tokens or not right_tokens:
            return 0
        overlap = len(left_tokens & right_tokens)
        if overlap <= 0:
            return 0
        return overlap * 18

    @classmethod
    def _surface_tokens(cls, surface: str) -> set[str]:
        compact = cls._compact_surface(surface)
        if not compact:
            return set()
        tokens = re.findall(r"[a-z]+|\d+|[\u4e00-\u9fff]{1,4}", compact)
        return {token for token in tokens if token}

    @staticmethod
    def _normalize_capacity(token: str) -> str:
        text = str(token or "").strip().upper().replace(" ", "")
        if not text:
            return ""
        text = text.replace("GB", "G").replace("TB", "T")
        match = re.fullmatch(r"(\d+)(?:G)?\+(\d+)(G|T)", text)
        if match:
            return f"{match.group(1)}+{match.group(2)}{match.group(3)}"
        match = re.fullmatch(r"(\d+)(G|T)", text)
        if match:
            return f"{match.group(1)}{match.group(2)}"
        return text

    @classmethod
    def _extract_capacity_hints(cls, query: str) -> set[str]:
        raw_query = str(query or "")
        matches = re.findall(r"(?i)\d+(?:G)?\+\d+(?:GB|G|TB|T)|\d+(?:GB|G|TB|T)", raw_query)
        hints = {cls._normalize_capacity(item) for item in matches if cls._normalize_capacity(item)}
        for token in re.split(r"[\s/,_|+-]+", raw_query):
            clean = str(token or "").strip()
            if not clean or not clean.isdigit():
                continue
            value = int(clean)
            if value in PURE_STORAGE_CAPACITY_VALUES:
                hints.add(f"{value}G")
        return hints

    @classmethod
    def _extract_keyword_hints(cls, query: str) -> list[str]:
        stripped = re.sub(r"(?i)\d+(?:G)?\+\d+(?:GB|G|TB|T)|\d+(?:GB|G|TB|T)", " ", str(query or ""))
        parts = re.split(r"[\s/,_|+-]+", stripped)
        hints: list[str] = []
        seen: set[str] = set()
        for part in parts:
            compact = cls._compact_surface(part)
            if not compact or compact in seen:
                continue
            seen.add(compact)
            hints.append(compact)
        return hints

    @classmethod
    def _select_image_refinement_query(
        cls,
        *,
        primary_query: str,
        matched_query: str,
        candidate: dict[str, object],
        preferred_brand: str,
        preferred_family: str,
    ) -> str:
        for query in (primary_query, matched_query):
            if cls._has_image_refinement_signal(
                query=query,
                candidate=candidate,
                preferred_brand=preferred_brand,
                preferred_family=preferred_family,
            ):
                return query
        return ""

    @classmethod
    def _has_image_refinement_signal(
        cls,
        *,
        query: str,
        candidate: dict[str, object],
        preferred_brand: str,
        preferred_family: str,
    ) -> bool:
        raw_query = str(query or "").strip()
        if not raw_query:
            return False
        if cls._extract_capacity_hints(raw_query):
            return True
        meaningful_hints = cls._contextual_keyword_hints(
            query=raw_query,
            candidate=candidate,
            preferred_brand=preferred_brand,
            preferred_family=preferred_family,
        )
        return bool(meaningful_hints)

    @staticmethod
    def _looks_like_spec_hint(hint: str) -> bool:
        compact = str(hint or "").strip().casefold()
        if not compact:
            return False
        if re.search(r"\d+\s*(g|gb|t|tb|w|mm|hz|寸)", compact):
            return True
        if re.search(r"[a-z]+\d+", compact):
            return True
        if re.fullmatch(r"\d+", compact):
            return True
        spec_tokens = (
            "黑",
            "白",
            "银",
            "灰",
            "蓝",
            "紫",
            "橙",
            "绿",
            "金",
            "粉",
            "红",
            "色",
            "gps",
            "esim",
            "蜂窝",
            "触屏",
            "纳米",
            "玻璃",
            "rtx",
            "core",
            "ultra",
            "pro",
            "max",
            "plus",
            "mini",
            "hunter",
            "gray",
            "grey",
            "silver",
            "black",
            "white",
            "blue",
            "purple",
            "orange",
            "green",
            "降噪",
            "标准",
            "标准款",
            "充电头",
            "数据线",
            "适配器",
            "充电盒",
            "左耳",
            "右耳",
            "洗地机",
            "吸头",
            "fluffy",
            "detect",
            "absolute",
            "origin",
            "pencil",
            "noise",
            "cancel",
        )
        return any(token in compact for token in spec_tokens)

    @staticmethod
    def _strip_auto_refinement_prefix(reply_text: str) -> str:
        return strip_auto_refinement_prefix(reply_text)


def _dedupe_str_list(items: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for item in items:
        clean = str(item or "").strip()
        if not clean:
            continue
        key = clean.casefold()
        if key in seen:
            continue
        seen.add(key)
        ordered.append(clean)
    return ordered
