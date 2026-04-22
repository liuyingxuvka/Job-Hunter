from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from ...prompt_assets import load_prompt_asset
from ...ai.client import build_json_schema_request
from .scoring import overall_analysis_score, to_fit_level_cn, unified_recommend_threshold
from .scoring_contract import normalize_score

FIT_LEVEL_VALUES = ("强匹配", "匹配", "可能匹配", "不匹配")
FIT_TRACK_VALUES = (
    "direct_fit",
    "adjacent_fit",
    "transferable_fit",
    "exploratory_fit",
)


@dataclass(frozen=True)
class TargetRoleDefinition:
    role_id: str
    profile_id: int | None = None
    name_zh: str = ""
    name_en: str = ""
    display_name: str = ""
    target_role_text: str = ""
    summary: str = ""
    scope_profile: str = ""

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> TargetRoleDefinition | None:
        role_id = str(payload.get("roleId") or "").strip()
        if not role_id:
            return None
        raw_profile_id = payload.get("profileId")
        profile_id: int | None
        if isinstance(raw_profile_id, bool):
            profile_id = None
        elif isinstance(raw_profile_id, int):
            profile_id = raw_profile_id
        elif isinstance(raw_profile_id, float) and raw_profile_id.is_integer():
            profile_id = int(raw_profile_id)
        else:
            try:
                text = str(raw_profile_id or "").strip()
                profile_id = int(text) if text else None
            except (TypeError, ValueError):
                profile_id = None
        return cls(
            role_id=role_id,
            profile_id=profile_id,
            name_zh=str(payload.get("nameZh") or "").strip(),
            name_en=str(payload.get("nameEn") or "").strip(),
            display_name=str(payload.get("displayName") or "").strip(),
            target_role_text=str(payload.get("targetRoleText") or "").strip(),
            summary=str(payload.get("summary") or "").strip(),
            scope_profile=str(payload.get("scopeProfile") or "").strip(),
        )

    def to_payload(self) -> dict[str, Any]:
        return {
            "roleId": self.role_id,
            "profileId": self.profile_id,
            "nameZh": self.name_zh,
            "nameEn": self.name_en,
            "displayName": self.display_name,
            "targetRoleText": self.target_role_text,
            "summary": self.summary,
            "scopeProfile": self.scope_profile,
        }


@dataclass(frozen=True)
class TargetRoleEvaluation:
    role_id: str
    profile_id: int | None = None
    name_zh: str = ""
    name_en: str = ""
    display_name: str = ""
    target_role_text: str = ""
    summary: str = ""
    scope_profile: str = ""
    score: int = 0
    fit_level_cn: str = "不匹配"
    recommend: bool = False
    reason_cn: str = ""

    @classmethod
    def from_role_definition(
        cls,
        role: TargetRoleDefinition,
        *,
        score: Any = 0,
        fit_level_cn: str = "",
        recommend: bool = False,
        reason_cn: str = "",
    ) -> TargetRoleEvaluation:
        normalized_score = normalize_score(score)
        normalized_fit_level = (
            fit_level_cn.strip()
            if isinstance(fit_level_cn, str) and fit_level_cn.strip() in FIT_LEVEL_VALUES
            else to_fit_level_cn(normalized_score)
        )
        return cls(
            role_id=role.role_id,
            profile_id=role.profile_id,
            name_zh=role.name_zh,
            name_en=role.name_en,
            display_name=role.display_name,
            target_role_text=role.target_role_text,
            summary=role.summary,
            scope_profile=role.scope_profile,
            score=normalized_score,
            fit_level_cn=normalized_fit_level,
            recommend=bool(recommend),
            reason_cn=str(reason_cn or "").strip(),
        )

    def to_payload(self) -> dict[str, Any]:
        return {
            "roleId": self.role_id,
            "profileId": self.profile_id,
            "nameZh": self.name_zh,
            "nameEn": self.name_en,
            "displayName": self.display_name,
            "targetRoleText": self.target_role_text,
            "summary": self.summary,
            "scopeProfile": self.scope_profile,
            "score": self.score,
            "fitLevelCn": self.fit_level_cn,
            "recommend": self.recommend,
            "reasonCn": self.reason_cn,
        }


@dataclass(frozen=True)
class TargetRoleBindingResult:
    best_role: TargetRoleEvaluation
    evaluations: tuple[TargetRoleEvaluation, ...]

    def to_payload(self) -> dict[str, Any]:
        return {
            "bestRole": self.best_role.to_payload(),
            "evaluations": [item.to_payload() for item in self.evaluations],
        }


def unified_overall_scoring_rubric(
    *,
    recommend_threshold: int,
    role_focus_note: str = "",
) -> str:
    note = f"\n- 额外关注：{role_focus_note}" if str(role_focus_note or "").strip() else ""
    return load_prompt_asset(
        "search_analysis",
        "unified_overall_scoring_rubric.txt",
    ).format(
        recommend_threshold=int(recommend_threshold),
        role_focus_note_block=note,
    ).strip()


def fit_track_prompt_note() -> str:
    return load_prompt_asset("search_analysis", "fit_track_prompt_note.txt")


def target_role_binding_min_score(config: Mapping[str, Any] | None) -> int:
    analysis = config.get("analysis") if isinstance(config, Mapping) else None
    default_threshold = unified_recommend_threshold(config)
    if isinstance(analysis, Mapping) and "targetRoleBindingMinScore" in analysis:
        return normalize_score(analysis.get("targetRoleBindingMinScore"), default=default_threshold)
    return default_threshold


def build_lite_scoring_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "matchScore": {"type": "integer", "minimum": 0, "maximum": 100},
            "recommend": {"type": "boolean"},
            "isJobPosting": {"type": "boolean"},
            "location": {"type": "string"},
            "fitTrack": {"type": "string"},
            "transferableScore": {"type": "integer", "minimum": 0, "maximum": 100},
            "primaryEvidenceCn": {"type": "string"},
        },
        "required": [
            "matchScore",
            "recommend",
            "isJobPosting",
            "location",
            "fitTrack",
            "transferableScore",
            "primaryEvidenceCn",
        ],
    }


def build_full_scoring_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "matchScore": {"type": "integer", "minimum": 0, "maximum": 100},
            "fitLevelCn": {"type": "string", "enum": list(FIT_LEVEL_VALUES)},
            "isJobPosting": {"type": "boolean"},
            "jobPostingEvidenceCn": {"type": "string"},
            "recommend": {"type": "boolean"},
            "recommendReasonCn": {"type": "string"},
            "location": {"type": "string"},
            "fitTrack": {"type": "string", "enum": list(FIT_TRACK_VALUES)},
            "transferableScore": {"type": "integer", "minimum": 0, "maximum": 100},
            "primaryEvidenceCn": {"type": "string"},
            "summaryCn": {"type": "string"},
            "reasonsCn": {"type": "array", "items": {"type": "string"}},
            "gapsCn": {"type": "array", "items": {"type": "string"}},
            "questionsCn": {"type": "array", "items": {"type": "string"}},
            "nextActionCn": {"type": "string"},
        },
        "required": [
            "matchScore",
            "fitLevelCn",
            "isJobPosting",
            "jobPostingEvidenceCn",
            "recommend",
            "recommendReasonCn",
            "location",
            "fitTrack",
            "transferableScore",
            "primaryEvidenceCn",
            "summaryCn",
            "reasonsCn",
            "gapsCn",
            "questionsCn",
            "nextActionCn",
        ],
    }


def build_target_role_binding_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "bestRoleId": {"type": "string"},
            "evaluations": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "roleId": {"type": "string"},
                        "score": {"type": "integer", "minimum": 0, "maximum": 100},
                        "fitLevelCn": {"type": "string", "enum": list(FIT_LEVEL_VALUES)},
                        "recommend": {"type": "boolean"},
                        "reasonCn": {"type": "string"},
                    },
                    "required": ["roleId", "score", "fitLevelCn", "recommend", "reasonCn"],
                },
            },
        },
        "required": ["bestRoleId", "evaluations"],
    }


def build_post_verify_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "isValidJobPage": {"type": "boolean"},
            "recommend": {"type": "boolean"},
            "location": {"type": "string"},
            "finalUrl": {"type": "string"},
        },
        "required": ["isValidJobPage", "recommend", "location", "finalUrl"],
    }


def build_lite_scoring_prompt(
    *,
    config: Mapping[str, Any],
    candidate_profile: Mapping[str, Any] | None,
    job: Mapping[str, Any],
    jd_text: str,
    jd_limit: int,
    data_availability_note: str = "",
) -> str:
    recommend_threshold = unified_recommend_threshold(config)
    return (
        "你是招聘筛选器。请做“低token极简评估”，输出JSON字段："
        "matchScore、recommend、isJobPosting、location、fitTrack、transferableScore、primaryEvidenceCn。\n"
        "不要输出任何理由、解释、列表、额外文字。\n\n"
        "候选人画像（JSON）：\n"
        f"{_json_block(candidate_profile or {})}\n\n"
        "候选人目标方向：\n"
        f"{_candidate_target_role_summary(config, fallback='未提供')}\n\n"
        "候选人地点偏好：\n"
        f"{_candidate_field(config, 'locationPreference', fallback='未提供')}\n\n"
        f"{str(data_availability_note or '').strip()}\n\n"
        "岗位：\n"
        f"Title: {_string_value(job, 'title')}\n"
        f"Company: {_string_value(job, 'company')}\n"
        f"Location: {_string_value(job, 'location')}\n"
        f"URL: {_string_value(job, 'url')}\n"
        "Search Summary:\n"
        f"{_truncate_text(_string_value(job, 'summary'), 1200)}\n"
        "JD:\n"
        f"{_truncate_text(jd_text, jd_limit)}\n\n"
        "规则：\n"
        f"- {unified_overall_scoring_rubric(recommend_threshold=recommend_threshold, role_focus_note='优先看岗位主体职责是否与候选人画像中的目标方向、核心能力、背景关键词和相邻方向整体一致。')}\n"
        "- location 为空时返回空字符串\n"
        f"{fit_track_prompt_note()}\n"
        "- transferableScore 0-100，仅作为辅助观察字段，不决定最终 recommend\n"
        "- primaryEvidenceCn 用一句中文给出主匹配证据\n"
        "只输出 JSON。"
    )


def build_full_scoring_prompt(
    *,
    config: Mapping[str, Any],
    candidate_profile: Mapping[str, Any] | None,
    job: Mapping[str, Any],
    jd_text: str,
    jd_limit: int,
    recommend_threshold: int,
    data_availability_note: str = "",
) -> str:
    return (
        "你是岗位招聘复核器。\n"
        "必须使用 web_search 访问该岗位 URL（以及必要的公司招聘入口），结合网页内容与提供的 JD 文本，做出结构化评估。\n"
        "注意：必须用中文输出；公司名/产品名/缩写保持英文。\n"
        f"{unified_overall_scoring_rubric(recommend_threshold=recommend_threshold, role_focus_note='优先看岗位主体职责是否与候选人画像中的目标方向、核心能力、背景关键词和相邻方向整体一致。')}\n\n"
        "候选人画像（JSON）：\n"
        f"{_json_block(candidate_profile or {})}\n\n"
        "候选人地点偏好：\n"
        f"{_candidate_field(config, 'locationPreference', fallback='未提供')}\n\n"
        f"{str(data_availability_note or '').strip()}\n\n"
        "岗位信息：\n"
        f"Title: {_string_value(job, 'title')}\n"
        f"Company: {_string_value(job, 'company')}\n"
        f"Location: {_string_value(job, 'location')}\n"
        f"URL: {_string_value(job, 'url')}\n"
        "Search Summary:\n"
        f"{_truncate_text(_string_value(job, 'summary'), 1500)}\n"
        "JD text:\n"
        f"{_truncate_text(jd_text, jd_limit)}\n\n"
        "请输出符合 schema 的 JSON，字段含义如下：\n"
        "- matchScore: 0-100\n"
        "- fitLevelCn: 强匹配/匹配/可能匹配/不匹配\n"
        "- isJobPosting: 该URL是否为真实可投递岗位JD页面（不是产品页/新闻页/公司介绍页/聚合镜像页）；对于 LinkedIn 这类职业平台上的具体职位页，也可以算岗位页\n"
        "- jobPostingEvidenceCn: 你判定为“岗位页/非岗位页”的依据（中文，简短）\n"
        "- recommend: 是否推荐申请\n"
        "- recommendReasonCn: 推荐/不推荐的简短理由\n"
        "- location: 岗位地点（尽量从网页中提取；多地/远程请写 Remote/Multiple/Global）\n"
        f"{fit_track_prompt_note()}\n"
        "- transferableScore: 0-100，可迁移能力匹配强度\n"
        "- primaryEvidenceCn: 主匹配证据（中文一句）\n"
        "- summaryCn: 该岗位一句话中文总结\n"
        "- reasonsCn: 匹配点（中文）\n"
        "- gapsCn: 主要差距（中文）\n"
        "- questionsCn: 建议对HR/用人经理提问的问题（中文）\n"
        "- nextActionCn: 下一步建议（中文）\n"
        "- 地点权重很重要。若岗位是明确 onsite / 固定办公地，且地点不在候选人偏好范围内，应明显降分，通常不推荐。\n"
        "- 若岗位为 Remote / Hybrid / Multiple，需要结合候选人的地点偏好判断是否可接受。\n"
        f"推荐规则：只有当 isJobPosting=true 且 matchScore ≥ {recommend_threshold} 时，才能 recommend=true；否则 recommend=false。\n\n"
        "只输出 JSON。"
    )


def build_target_role_binding_prompt(
    *,
    config: Mapping[str, Any],
    candidate_profile: Mapping[str, Any] | None,
    job: Mapping[str, Any],
    jd_text: str,
    jd_limit: int,
    overall_analysis: Mapping[str, Any] | None,
    target_roles: Sequence[TargetRoleDefinition | Mapping[str, Any]],
    recommend_threshold: int,
    data_availability_note: str = "",
) -> str:
    normalized_roles = normalize_target_roles(target_roles)
    if candidate_profile:
        candidate_context = f"候选人画像（JSON）：\n{_json_block(candidate_profile)}"
    else:
        candidate_context = (
            "候选人整体方向：\n"
            f"{_candidate_target_role_summary(config, fallback='未提供')}\n\n"
            "候选人地点偏好：\n"
            f"{_candidate_field(config, 'locationPreference', fallback='未提供')}"
        )
    role_payload = [role.to_payload() for role in normalized_roles]
    overall_payload = {
        "overallScore": overall_analysis_score(overall_analysis or {}),
        "overallFitLevelCn": str((overall_analysis or {}).get("fitLevelCn") or "").strip(),
        "overallRecommend": bool((overall_analysis or {}).get("recommend")),
        "primaryEvidenceCn": str((overall_analysis or {}).get("primaryEvidenceCn") or "").strip(),
        "fitTrack": str((overall_analysis or {}).get("fitTrack") or "").strip(),
    }
    return (
        "你是职位匹配评审器。你的任务不是重做整套推荐，而是基于已经通过的整体粗筛结果，\n"
        "把同一个岗位分别映射到当前启用的 target roles，并选出唯一最合适的一项。\n\n"
        "要求：\n"
        "1. 必须对每个 target role 单独评分，不能把多个角色合并成一个总标签。\n"
        "2. score 是“该岗位与这个 target role 的专向匹配度”，不是整体岗位质量。\n"
        "3. bestRoleId 必须从输入的 roleId 中选择。\n"
        "4. 如果所有角色都不强匹配，也要选出相对最合适的一项，但低分要如实给出。\n"
        "5. 这一阶段只负责“绑定到最接近的 target role”，不负责决定岗位是否进入最终推荐列表，也不要改写 matchScore。\n"
        "6. recommend 只表示“若按这个 target role 来看，是否属于比较明确的方向”，仅供解释，不决定岗位是否入表。\n\n"
        "统一专向打分标准：\n"
        "- score 表示“岗位与该 target role 的专向匹配度”，不是整体岗位质量。\n"
        "- 85-100：与该目标岗位高度直接匹配。\n"
        "- 70-84：与该目标岗位明显匹配，可优先考虑。\n"
        "- 50-69：与该目标岗位存在较强相关性，虽然不一定完全对口，但可以视为合理方向。\n"
        "- 30-49：与该目标岗位有部分交叉，但不是主要对口方向。\n"
        "- 0-29：基本不匹配。\n"
        "- 即使岗位不是完全对口，也必须选出最接近的一项，不能因为不完美匹配就把所有角色都压成极低分。\n"
        "- 只要能解释“为什么这个岗位更接近这个方向”，就可以给出相应分数；不要把第二阶段当成再次硬筛。\n\n"
        f"{candidate_context}\n\n"
        "整体粗筛结果（JSON）：\n"
        f"{_json_block(overall_payload)}\n\n"
        f"{str(data_availability_note or '').strip()}\n\n"
        "当前启用的 target roles（JSON）：\n"
        f"{_json_block(role_payload)}\n\n"
        "岗位信息：\n"
        f"Title: {_string_value(job, 'title')}\n"
        f"Company: {_string_value(job, 'company')}\n"
        f"Location: {_string_value(job, 'location')}\n"
        f"URL: {_string_value(job, 'url')}\n"
        "Search Summary:\n"
        f"{_truncate_text(_string_value(job, 'summary'), 1400)}\n"
        "JD text:\n"
        f"{_truncate_text(jd_text, jd_limit)}\n\n"
        "输出要求：\n"
        "- bestRoleId: 最合适角色的 roleId\n"
        "- evaluations: 每个 target role 一条结果\n"
        "- fitLevelCn: 强匹配 / 匹配 / 可能匹配 / 不匹配\n"
        f"- recommend: 仅表示该 role 是否属于比较明确的方向，通常该 role score >= {recommend_threshold} 时可为 true；它不决定岗位是否入表\n"
        "- reasonCn: 用一句中文说明为什么这个岗位与该角色匹配或不匹配\n\n"
        "只输出 JSON。"
    )


def build_post_verify_prompt(
    *,
    config: Mapping[str, Any],
    job: Mapping[str, Any],
    jd_text: str,
    jd_limit: int,
    recommend_threshold: int,
) -> str:
    recommend_rule = "岗位主体职责仍需与候选人的目标方向、核心能力、背景关键词和相邻方向整体画像明显匹配；不能只因为零散关键词命中就保留。"
    return (
        "你是岗位复核器。请只做二次复核，输出 JSON。\n\n"
        "候选人目标：\n"
        f"{_candidate_target_role_summary(config, fallback='未提供')}\n\n"
        "候选人地点偏好：\n"
        f"{_candidate_field(config, 'locationPreference', fallback='未提供')}\n\n"
        "岗位：\n"
        f"Title: {_string_value(job, 'title')}\n"
        f"Company: {_string_value(job, 'company')}\n"
        f"Location: {_string_value(job, 'location')}\n"
        f"URL: {_string_value(job, 'url')}\n"
        "JD:\n"
        f"{_truncate_text(jd_text, jd_limit)}\n\n"
        "判定规则：\n"
        "1) isValidJobPage=true 仅当该链接是“真实可投递岗位JD页”（不是 careers 首页/职位列表入口/新闻/聚合页/失效页/反爬拦截页）。\n"
        f"2) recommend=true 仅当 isValidJobPage=true、岗位与候选人整体画像明显匹配，且地点与候选人的地点偏好不明显冲突：{recommend_rule}\n"
        f"3) 若只是少量关键词相关、但岗位主体职责不对，必须 recommend=false。当前主评分阈值是 {recommend_threshold}，复核时请沿用相同口径做保守判断。\n"
        "4) finalUrl 返回你确认后的最终岗位URL，优先返回 employer/ATS 的具体岗位详情/投递页；不要返回 careers 首页、搜索列表页、地区筛选页或职业聚合页。无法确认就返回原URL。\n"
        "5) location 尽量给出岗位地点，未知可空字符串。\n\n"
        "只输出 JSON。"
    )


def build_lite_scoring_request(
    *,
    model: str,
    config: Mapping[str, Any],
    candidate_profile: Mapping[str, Any] | None,
    job: Mapping[str, Any],
    jd_text: str,
    jd_limit: int,
    data_availability_note: str = "",
    use_web_search: bool = False,
) -> dict[str, Any]:
    return build_json_schema_request(
        model=model,
        input_payload=build_lite_scoring_prompt(
            config=config,
            candidate_profile=candidate_profile,
            job=job,
            jd_text=jd_text,
            jd_limit=jd_limit,
            data_availability_note=data_availability_note,
        ),
        schema_name="job_fit_score_lite",
        schema=build_lite_scoring_schema(),
        use_web_search=use_web_search,
    )


def build_full_scoring_request(
    *,
    model: str,
    config: Mapping[str, Any],
    candidate_profile: Mapping[str, Any] | None,
    job: Mapping[str, Any],
    jd_text: str,
    jd_limit: int,
    recommend_threshold: int | None = None,
    data_availability_note: str = "",
    use_web_search: bool = False,
) -> dict[str, Any]:
    effective_threshold = (
        unified_recommend_threshold(config)
        if recommend_threshold is None
        else normalize_score(recommend_threshold, default=unified_recommend_threshold(config))
    )
    return build_json_schema_request(
        model=model,
        input_payload=build_full_scoring_prompt(
            config=config,
            candidate_profile=candidate_profile,
            job=job,
            jd_text=jd_text,
            jd_limit=jd_limit,
            recommend_threshold=effective_threshold,
            data_availability_note=data_availability_note,
        ),
        schema_name="job_fit_score",
        schema=build_full_scoring_schema(),
        use_web_search=use_web_search,
    )


def build_target_role_binding_request(
    *,
    model: str,
    config: Mapping[str, Any],
    candidate_profile: Mapping[str, Any] | None,
    job: Mapping[str, Any],
    jd_text: str,
    jd_limit: int,
    overall_analysis: Mapping[str, Any] | None,
    target_roles: Sequence[TargetRoleDefinition | Mapping[str, Any]],
    recommend_threshold: int | None = None,
) -> dict[str, Any]:
    effective_threshold = (
        unified_recommend_threshold(config)
        if recommend_threshold is None
        else normalize_score(recommend_threshold, default=unified_recommend_threshold(config))
    )
    return build_json_schema_request(
        model=model,
        input_payload=build_target_role_binding_prompt(
            config=config,
            candidate_profile=candidate_profile,
            job=job,
            jd_text=jd_text,
            jd_limit=jd_limit,
            overall_analysis=overall_analysis,
            target_roles=target_roles,
            recommend_threshold=effective_threshold,
        ),
        schema_name="target_role_binding",
        schema=build_target_role_binding_schema(),
        use_web_search=False,
    )


def build_post_verify_request(
    *,
    model: str,
    config: Mapping[str, Any],
    job: Mapping[str, Any],
    jd_text: str,
    jd_limit: int,
    recommend_threshold: int | None = None,
    use_web_search: bool = False,
) -> dict[str, Any]:
    effective_threshold = (
        unified_recommend_threshold(config)
        if recommend_threshold is None
        else normalize_score(recommend_threshold, default=unified_recommend_threshold(config))
    )
    return build_json_schema_request(
        model=model,
        input_payload=build_post_verify_prompt(
            config=config,
            job=job,
            jd_text=jd_text,
            jd_limit=jd_limit,
            recommend_threshold=effective_threshold,
        ),
        schema_name="post_verify_recommended_job",
        schema=build_post_verify_schema(),
        use_web_search=use_web_search,
    )


def normalize_lite_scoring_payload(
    payload: Mapping[str, Any],
    *,
    recommend_threshold: int,
) -> dict[str, Any]:
    score = normalize_score(payload.get("matchScore"))
    is_job_posting = payload.get("isJobPosting") is True
    return _apply_overall_scoring_contract(
        {
        "matchScore": score,
            "isJobPosting": is_job_posting,
            "jobPostingEvidenceCn": "低token模式判定为岗位JD页面。"
            if is_job_posting
            else "低token模式判定为非岗位JD页面。",
            "recommend": bool(payload.get("recommend") is True and is_job_posting and score >= recommend_threshold),
            "recommendReasonCn": "",
            "location": str(payload.get("location") or "").strip(),
            "fitTrack": _normalize_fit_track(payload.get("fitTrack")),
            "transferableScore": normalize_score(payload.get("transferableScore")),
            "primaryEvidenceCn": str(payload.get("primaryEvidenceCn") or "").strip(),
            "summaryCn": "",
            "reasonsCn": [],
            "gapsCn": [],
            "questionsCn": [],
            "nextActionCn": "",
        },
        score=score,
        fit_level_cn=to_fit_level_cn(score),
    )


def normalize_full_scoring_payload(
    payload: Mapping[str, Any],
    *,
    recommend_threshold: int,
) -> dict[str, Any]:
    score = normalize_score(payload.get("matchScore"))
    fit_level = str(payload.get("fitLevelCn") or "").strip()
    is_job_posting = payload.get("isJobPosting") is True
    if fit_level not in FIT_LEVEL_VALUES:
        fit_level = to_fit_level_cn(score)
    return _apply_overall_scoring_contract(
        {
        "matchScore": score,
            "isJobPosting": is_job_posting,
            "jobPostingEvidenceCn": str(payload.get("jobPostingEvidenceCn") or "").strip(),
            "recommend": bool(payload.get("recommend") is True and is_job_posting and score >= recommend_threshold),
            "recommendReasonCn": str(payload.get("recommendReasonCn") or "").strip(),
            "location": str(payload.get("location") or "").strip(),
            "fitTrack": _normalize_fit_track(payload.get("fitTrack")),
            "transferableScore": normalize_score(payload.get("transferableScore")),
            "primaryEvidenceCn": str(payload.get("primaryEvidenceCn") or "").strip(),
            "summaryCn": str(payload.get("summaryCn") or "").strip(),
            "reasonsCn": _normalize_string_list(payload.get("reasonsCn")),
            "gapsCn": _normalize_string_list(payload.get("gapsCn")),
            "questionsCn": _normalize_string_list(payload.get("questionsCn")),
            "nextActionCn": str(payload.get("nextActionCn") or "").strip(),
        },
        score=score,
        fit_level_cn=fit_level,
    )


def normalize_target_role_binding_payload(
    payload: Mapping[str, Any],
    *,
    target_roles: Sequence[TargetRoleDefinition | Mapping[str, Any]],
    recommend_threshold: int,
) -> TargetRoleBindingResult | None:
    normalized_roles = normalize_target_roles(target_roles)
    if not normalized_roles:
        return None
    parsed_evaluations = payload.get("evaluations")
    if not isinstance(parsed_evaluations, Sequence) or isinstance(parsed_evaluations, (str, bytes, bytearray)):
        parsed_evaluations = []
    evaluations: list[TargetRoleEvaluation] = []
    for role in normalized_roles:
        matched = {}
        for item in parsed_evaluations:
            if not isinstance(item, Mapping):
                continue
            if str(item.get("roleId") or "").strip() == role.role_id:
                matched = item
                break
        score = normalize_score(matched.get("score"))
        evaluations.append(
            TargetRoleEvaluation.from_role_definition(
                role,
                score=score,
                fit_level_cn=str(matched.get("fitLevelCn") or "").strip(),
                recommend=bool(matched.get("recommend") is True and score >= recommend_threshold),
                reason_cn=str(matched.get("reasonCn") or "").strip(),
            )
        )
    requested_best_role_id = str(payload.get("bestRoleId") or "").strip()
    best_role = next((item for item in evaluations if item.role_id == requested_best_role_id), None)
    if best_role is None:
        ranked = sorted(
            evaluations,
            key=lambda item: (-item.score, -int(item.recommend), item.display_name.casefold()),
        )
        best_role = ranked[0] if ranked else None
    if best_role is None:
        return None
    return TargetRoleBindingResult(best_role=best_role, evaluations=tuple(evaluations))


def normalize_post_verify_payload(payload: Mapping[str, Any], *, job_url: str = "") -> dict[str, Any]:
    return {
        "isValidJobPage": payload.get("isValidJobPage") is True,
        "recommend": payload.get("recommend") is True,
        "location": str(payload.get("location") or "").strip(),
        "finalUrl": str(payload.get("finalUrl") or job_url or "").strip(),
    }


def _resolve_overall_analysis_score(payload: Mapping[str, Any]) -> int:
    return normalize_score(
        payload.get(
            "overallScore",
            payload.get("matchScore", payload.get("score")),
        )
    )


def _apply_overall_scoring_contract(
    payload: Mapping[str, Any],
    *,
    score: Any | None = None,
    fit_level_cn: str | None = None,
) -> dict[str, Any]:
    normalized = dict(payload)
    overall_score = _resolve_overall_analysis_score(normalized) if score is None else normalize_score(score)
    overall_fit_level = str(
        fit_level_cn or normalized.get("overallFitLevelCn") or normalized.get("fitLevelCn") or ""
    ).strip()
    if not overall_fit_level:
        overall_fit_level = to_fit_level_cn(overall_score)
    normalized["overallScore"] = overall_score
    normalized["matchScore"] = overall_score
    normalized["overallFitLevelCn"] = overall_fit_level
    normalized["fitLevelCn"] = overall_fit_level
    return normalized


def apply_target_role_binding_to_analysis(
    analysis: Mapping[str, Any],
    role_binding: TargetRoleBindingResult | Mapping[str, Any] | None,
    *,
    config: Mapping[str, Any],
) -> dict[str, Any]:
    normalized_binding = _coerce_role_binding_result(role_binding)
    if normalized_binding is None:
        return _apply_overall_scoring_contract(analysis)
    best_role = normalized_binding.best_role
    recommend_threshold = unified_recommend_threshold(config)
    overall_score = _resolve_overall_analysis_score(analysis)
    role_score = normalize_score(best_role.score, default=overall_score)
    role_fit_level = best_role.fit_level_cn.strip() or to_fit_level_cn(role_score)
    overall_fit_level = str(analysis.get("overallFitLevelCn") or analysis.get("fitLevelCn") or "").strip()
    if not overall_fit_level:
        overall_fit_level = to_fit_level_cn(overall_score)
    recommend = analysis.get("recommend") is True
    recommend_reason = best_role.reason_cn.strip() or str(analysis.get("recommendReasonCn") or "").strip()
    normalized = _apply_overall_scoring_contract(
        {
            **dict(analysis),
            "recommend": recommend,
            "recommendReasonCn": recommend_reason,
        },
        score=overall_score,
        fit_level_cn=overall_fit_level,
    )
    normalized.update(
        {
        "targetRoleScore": role_score,
        "targetRoleFitLevelCn": role_fit_level,
        "boundTargetRole": {
            "roleId": best_role.role_id,
            "profileId": best_role.profile_id,
            "nameZh": best_role.name_zh,
            "nameEn": best_role.name_en,
            "displayName": best_role.display_name,
            "targetRoleText": best_role.target_role_text,
            "score": role_score,
            "fitLevelCn": role_fit_level,
            "recommend": bool(best_role.recommend and role_score >= recommend_threshold),
            "reasonCn": best_role.reason_cn,
        },
        "targetRoleScores": [item.to_payload() for item in normalized_binding.evaluations],
        }
    )
    return normalized


def prepare_analysis_for_storage(
    analysis: Mapping[str, Any],
    role_binding: TargetRoleBindingResult | Mapping[str, Any] | None,
    *,
    config: Mapping[str, Any],
) -> dict[str, Any]:
    base = _apply_overall_scoring_contract(analysis)
    normalized_binding = _coerce_role_binding_result(role_binding)
    if normalized_binding is None:
        return base
    return apply_target_role_binding_to_analysis(base, normalized_binding, config=config)


def normalize_target_roles(
    target_roles: Sequence[TargetRoleDefinition | Mapping[str, Any]],
) -> list[TargetRoleDefinition]:
    normalized: list[TargetRoleDefinition] = []
    seen: set[str] = set()
    for item in target_roles:
        role = item if isinstance(item, TargetRoleDefinition) else TargetRoleDefinition.from_payload(item)
        if role is None or role.role_id in seen:
            continue
        seen.add(role.role_id)
        normalized.append(role)
    return normalized


def extract_job_jd_text(job: Mapping[str, Any]) -> str:
    jd = job.get("jd")
    if isinstance(jd, Mapping):
        text = str(jd.get("text") or jd.get("rawText") or "").strip()
        if text:
            return text
    return str(job.get("summary") or job.get("snippet") or "").strip()


def _candidate_field(config: Mapping[str, Any], key: str, fallback: str = "") -> str:
    candidate = config.get("candidate")
    if not isinstance(candidate, Mapping):
        return fallback
    value = str(candidate.get(key) or "").strip()
    return value or fallback


def _candidate_target_roles(config: Mapping[str, Any]) -> list[TargetRoleDefinition]:
    candidate = config.get("candidate")
    if not isinstance(candidate, Mapping):
        return []
    target_roles = candidate.get("targetRoles")
    if not isinstance(target_roles, list):
        return []
    return normalize_target_roles(target_roles)


def _candidate_target_role_summary(config: Mapping[str, Any], fallback: str = "") -> str:
    normalized_roles = _candidate_target_roles(config)
    if normalized_roles:
        chunks: list[str] = []
        seen: set[str] = set()
        for role in normalized_roles:
            text = role.display_name or role.target_role_text or role.name_en or role.name_zh
            text = str(text or "").strip()
            if not text:
                continue
            key = text.casefold()
            if key in seen:
                continue
            seen.add(key)
            chunks.append(text)
        if chunks:
            return " ; ".join(chunks[:8])
    return fallback


def _json_block(value: Any) -> str:
    import json

    return json.dumps(value, ensure_ascii=False, indent=2)


def _string_value(payload: Mapping[str, Any], key: str) -> str:
    return str(payload.get(key) or "").strip()


def _truncate_text(text: str, limit: int) -> str:
    normalized = str(text or "")
    max_length = max(1, int(limit))
    if len(normalized) <= max_length:
        return normalized
    return normalized[:max_length].rstrip()


def _normalize_fit_track(value: Any) -> str:
    text = str(value or "").strip()
    return text if text in FIT_TRACK_VALUES else ""


def _normalize_string_list(value: Any) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    items: list[str] = []
    for item in value:
        text = str(item or "").strip()
        if text:
            items.append(text)
    return items


def _coerce_role_binding_result(
    value: TargetRoleBindingResult | Mapping[str, Any] | None,
) -> TargetRoleBindingResult | None:
    if isinstance(value, TargetRoleBindingResult):
        return value
    if not isinstance(value, Mapping):
        return None
    best_role_payload = value.get("bestRole")
    evaluations_payload = value.get("evaluations")
    if not isinstance(best_role_payload, Mapping):
        return None
    if not isinstance(evaluations_payload, Sequence) or isinstance(
        evaluations_payload, (str, bytes, bytearray)
    ):
        evaluations_payload = []
    normalized_evaluations: list[TargetRoleEvaluation] = []
    for item in evaluations_payload:
        if not isinstance(item, Mapping):
            continue
        role = TargetRoleDefinition.from_payload(item)
        if role is None:
            continue
        normalized_evaluations.append(
            TargetRoleEvaluation.from_role_definition(
                role,
                score=item.get("score"),
                fit_level_cn=str(item.get("fitLevelCn") or "").strip(),
                recommend=item.get("recommend") is True,
                reason_cn=str(item.get("reasonCn") or "").strip(),
            )
        )
    best_role_definition = TargetRoleDefinition.from_payload(best_role_payload)
    if best_role_definition is None:
        return None
    best_role = TargetRoleEvaluation.from_role_definition(
        best_role_definition,
        score=best_role_payload.get("score"),
        fit_level_cn=str(best_role_payload.get("fitLevelCn") or "").strip(),
        recommend=best_role_payload.get("recommend") is True,
        reason_cn=str(best_role_payload.get("reasonCn") or "").strip(),
    )
    if not normalized_evaluations:
        normalized_evaluations = [best_role]
    return TargetRoleBindingResult(best_role=best_role, evaluations=tuple(normalized_evaluations))


__all__ = [
    "FIT_LEVEL_VALUES",
    "FIT_TRACK_VALUES",
    "TargetRoleBindingResult",
    "TargetRoleDefinition",
    "TargetRoleEvaluation",
    "build_full_scoring_prompt",
    "build_full_scoring_request",
    "build_full_scoring_schema",
    "build_lite_scoring_prompt",
    "build_lite_scoring_request",
    "build_lite_scoring_schema",
    "build_post_verify_prompt",
    "build_post_verify_request",
    "build_post_verify_schema",
    "build_target_role_binding_prompt",
    "build_target_role_binding_request",
    "build_target_role_binding_schema",
    "extract_job_jd_text",
    "fit_track_prompt_note",
    "normalize_full_scoring_payload",
    "normalize_lite_scoring_payload",
    "normalize_post_verify_payload",
    "normalize_target_role_binding_payload",
    "normalize_target_roles",
    "prepare_analysis_for_storage",
    "apply_target_role_binding_to_analysis",
    "target_role_binding_min_score",
    "unified_overall_scoring_rubric",
]
