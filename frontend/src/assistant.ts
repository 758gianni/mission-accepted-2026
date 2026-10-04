import { filterCandidates, temporalClassOf, type CandidateFeature, type CandidateFilter, type DashboardContract } from './terra-data';

export interface CandidateQuery { filter: CandidateFilter; minAreaHa?: number; maxAreaHa?: number; inclusiveMin?: boolean; inclusiveMax?: boolean; sort?: 'priority' | 'area' }
export type AssistantAction = { type: 'select'; id: string } | { type: 'query'; query: CandidateQuery };
export interface AssistantContext { contract: DashboardContract; features: CandidateFeature[]; selectedId: string | number | null }
export interface AssistantReply {
	text: string;
	candidates: Array<{ id: string; label: string; summary: string }>;
	actions: Array<{ label: string; action: AssistantAction }>;
	sources: Array<{ label: string; href: string }>;
}
export interface AssistantProvider {
	name: string;
	answer(request: { question: string; context: AssistantContext }, signal?: AbortSignal): Promise<AssistantReply>;
}
const source = (label: string, candidate = false) => ({ label, href: `/data/derived/${candidate ? 'candidate_regions.geojson' : 'contract.json'}` });
const number = (value: unknown, digits = 1) => typeof value === 'number' && Number.isFinite(value) ? value.toLocaleString('en', { maximumFractionDigits: digits }) : 'not supplied';
const finite = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value);
const bytes = (value: unknown) => {
	if (!finite(value) || value < 0) return 'not supplied';
	const exponent = value === 0 ? 0 : Math.min(4, Math.floor(Math.log10(value) / 3));
	return `${number(value / 1000 ** exponent)} ${['B', 'KB', 'MB', 'GB', 'TB'][exponent]}`;
};
const labels: Record<CandidateFilter, string> = { all: 'All anomalies', persistent: 'Persistent anomalies', validated: 'Independently supported anomalies', seasonal_transient: 'Seasonal / transient anomalies', late_unclassified: 'Late / new anomalies', qa_cleared: 'QA screen passes' };
export const queryLabel = (query: CandidateQuery) => `${labels[query.filter]}${query.minAreaHa != null ? ` · ${query.inclusiveMin ? '≥' : '>'} ${number(query.minAreaHa)} ha` : ''}${query.maxAreaHa != null ? ` · ${query.inclusiveMax ? '≤' : '<'} ${number(query.maxAreaHa)} ha` : ''}`;
export function queryCandidates(features: CandidateFeature[], query: CandidateQuery): CandidateFeature[] {
	return filterCandidates(features, query.filter).filter(({ properties: p }) =>
		(query.minAreaHa == null || finite(p.area_ha) && (query.inclusiveMin ? p.area_ha >= query.minAreaHa : p.area_ha > query.minAreaHa)) &&
		(query.maxAreaHa == null || finite(p.area_ha) && (query.inclusiveMax ? p.area_ha <= query.maxAreaHa : p.area_ha < query.maxAreaHa)))
		.sort((a, b) => {
			const key = query.sort === 'area' ? 'area_ha' : 'priority_score';
			return (finite(b.properties[key]) ? b.properties[key] : -Infinity) - (finite(a.properties[key]) ? a.properties[key] : -Infinity) || String(a.properties.id).localeCompare(String(b.properties.id));
		});
}
const reference = (feature: CandidateFeature) => ({ id: String(feature.properties.id), label: `Region ${feature.properties.id}`, summary: `${number(feature.properties.area_ha)} ha · priority ${number(feature.properties.priority_score)}` });
const reply = (text: string, sources: AssistantReply['sources'] = [], candidates: CandidateFeature[] = [], actions: AssistantReply['actions'] = []): AssistantReply => ({ text, sources, candidates: candidates.map(reference), actions });

function explain(feature: CandidateFeature, context: AssistantContext, hypothesis = false): AssistantReply {
	const p = feature.properties;
	const rank = queryCandidates(context.features, { filter: 'all' }).findIndex((item) => item === feature) + 1;
	const temporal = temporalClassOf(p);
	const classification = temporal === 'seasonal_transient' ? 'Seasonal / transient' : temporal === 'persistent' || temporal === 't4_validated_persistent' ? 'Persistent radar change' : temporal === 'late_unclassified' ? 'Late change; persistence is not established' : 'Unclassified radar change';
	const facts = [`Region ${p.id} is #${rank} by the catalogue’s stored priority score (${number(p.priority_score)}). This is a heuristic, not a confidence percentage; score weights are not supplied.`];
	facts.push(`${classification} · ${number(p.area_ha)} ha · ${number(p.mean_signed_db)} dB mean radar change.`);
	if (p.persists_into_T3 === false) facts.push('The stored temporal result returned toward baseline in the later dry-season observation. That supports the seasonal/transient class, without establishing a physical cause.');
	else if (p.persists_into_T3 === true) facts.push('The stored temporal result persists into the later dry-season observation.');
	const supported = p.t4_status === 'confirmed' || p.t4_status === 'moderate_support';
	const sourceIds = (p.t4_observation as { source_ids?: string[] } | undefined)?.source_ids ?? [];
	const observation = context.contract.acquisitions?.find((item) => item.source_ids?.some((id) => sourceIds.includes(id)));
	facts.push(supported ? `Independent support${p.t4_status === 'moderate_support' ? ' (moderate)' : ''}${observation ? `: ${observation.sensor ?? 'EO'} observation on ${observation.date}` : ' is recorded'}${finite(p.t4_mean_signed_db) ? `; ${number(p.t4_mean_signed_db)} dB` : ''}. This supports radar persistence, not a land-cover label.` : p.t4_status === 'outside_coverage' ? 'Outside independent observation coverage.' : 'Independent support is not recorded.');
	facts.push(`Terrain-risk fraction: ${number(p.terrain_risk_pct)}${finite(p.terrain_risk_pct) ? '%' : ''}. Candidate-specific registration clearance is not supplied in this catalogue.`);
	const sources = [source(`Candidate catalogue · Region ${p.id}`, true), source('Acquisition metadata and registration QA')];
	const note = context.contract.candidate_notes?.[String(p.id)];
	if (hypothesis) {
		if (note) {
			facts.unshift(`Unverified interpretation — ${note.hypothesis}. Stored project note (${note.attribution}): ${note.detail}`);
			sources.push(source(`Project note · Region ${p.id} · ${note.status}`));
		} else facts.unshift('No project interpretation is stored for this candidate. Radar change alone does not establish a physical cause.');
	}
	return reply(facts.join('\n\n'), sources, [feature]);
}

function answerLocal(question: string, context: AssistantContext): AssistantReply {
	const q = question.toLowerCase().replace(/,/g, '');
	const { contract, features } = context;
	const requestedId = question.match(/\bregion\s+#?([\w-]+)|\bcandidate\s+#?(\d[\w-]*)/i);
	const id = requestedId?.[1] ?? requestedId?.[2];
	const selected = features.find((item) => String(item.properties.id).toLowerCase() === String(id ?? context.selectedId).toLowerCase());
	if (id && !selected) return reply(`Region ${id} is not present in the current dataset.`, [source('Candidate catalogue', true)]);
	if (/deforest|flood|fire|burn scar|drought|prove.*cause|cause.*proven/.test(q)) {
		return reply('The current dataset records radar-change candidates. It does not establish deforestation, flooding, fire, or another physical cause. I can show the stored temporal, terrain, registration, and independent-observation evidence.', [source('Candidate evidence and scientific limitations', true)]);
	}
	if (/hypothes|interpretation|water body|new lake/.test(q)) {
		const noted = selected ?? features.find((item) => contract.candidate_notes?.[String(item.properties.id)]?.hypothesis.toLowerCase().includes('water'));
		return noted ? explain(noted, context, true) : reply('No matching interpretation is stored in the current dataset. Radar changes do not establish a new water body.', [source('Project notes')]);
	}
	if (selected && (id || /\bthis\b|selected|current anomaly/.test(q))) return explain(selected, context);
	if (/reduc|raw|derived|compress|storage|how much data/.test(q)) {
		const p = contract.processing ?? {};
		return reply(`Raw archive: ${bytes(p.raw_bytes)} → derived products: ${bytes(p.total_derived_bytes)}.\n\nMeasured reduction: ${finite(p.raw_to_all_derived_ratio) ? `${number(p.raw_to_all_derived_ratio)}×` : 'not supplied'}. The derived total includes analysis-ready, temporal, candidate, and terrain products.`, [source('Processing storage metrics')]);
	}
	if (/registration|\bqa\b|terrain/.test(q) && !/show|list|which|rank|top|filter/.test(q)) {
		const qa = contract.registration_qa;
		const pair = qa?.pair_checks;
		const subset = qa?.candidate_subset;
		return reply(pair ? `${pair.passing}/${pair.comparisons} tile/date comparisons have passing registration evidence; ${pair.insufficient} insufficient; ${pair.review_required} require measured review. Insufficient evidence is not a measured failure.${subset ? `\n\nSeparate screened subset: ${subset.registration_passing}/${subset.screened} candidates have registration support; ${subset.initial_screen_passing} pass the combined initial screen. These subset counts are not clearance for the entire catalogue.` : ''}` : 'Registration QA metrics are not supplied in this dataset.', [source('Registration QA · pair checks and candidate subset')]);
	}
	if (/how many|dataset|summary|results|coverage|acquisitions|observation dates|sensors/.test(q) && !/show|list|which|filter|rank/.test(q)) {
		const counts = contract.counts;
		const p = contract.tile_processing;
		return reply(`${features.length} catalogue detections: ${number(counts.seasonal_transient, 0)} seasonal/transient, ${number((counts.persistent ?? 0) + (counts.t4_validated_persistent ?? 0), 0)} persistent, including ${number(counts.t4_validated_persistent, 0)} independently supported.\n\n${p ? `${p.acquisition_count ?? 'Unspecified'} source acquisitions across ${p.acquisition_dates?.length ?? 'unspecified'} dates. Analysis-valid area: ${number(p.analysis_valid_area_km2, 0)} km²; satellite coverage: ${number(p.satellite_coverage_area_km2, 0)} km².` : `${contract.acquisitions?.length ?? 0} acquisition entries are supplied.`}`, [source('Dataset counts, coverage and acquisition metadata')]);
	}
	if (/show|list|which|rank|top|largest|filter|persistent|seasonal|independently supported/.test(q)) {
		const filter: CandidateFilter = /independent|validat|supported/.test(q) ? 'validated' : /seasonal|transient/.test(q) ? 'seasonal_transient' : /persistent/.test(q) ? 'persistent' : /qa.cleared|screen pass/.test(q) ? 'qa_cleared' : /late|new change/.test(q) ? 'late_unclassified' : 'all';
		const lower = q.match(/(larger than|greater than|over|above|at least|>=|>)\s*(\d+(?:\.\d+)?)\s*(ha|hectares?|km[²2])?/);
		const upper = q.match(/(smaller than|less than|under|below|at most|<=|<)\s*(\d+(?:\.\d+)?)\s*(ha|hectares?|km[²2])?/);
		const query: CandidateQuery = { filter, sort: /largest|biggest/.test(q) ? 'area' : 'priority' };
		if (lower) { query.minAreaHa = Number(lower[2]) * (lower[3]?.startsWith('km') ? 100 : 1); query.inclusiveMin = /at least|>=/.test(lower[1]); }
		if (upper) { query.maxAreaHa = Number(upper[2]) * (upper[3]?.startsWith('km') ? 100 : 1); query.inclusiveMax = /at most|<=/.test(upper[1]); }
		const matches = queryCandidates(features, query);
		const limit = Math.max(1, Math.min(10, Number(q.match(/\btop\s+(\d+)/)?.[1] ?? 5)));
		return reply(`${matches.length} matching candidates: ${queryLabel(query)}. ${matches.length ? `Showing ${Math.min(limit, matches.length)} ranked by ${query.sort === 'area' ? 'area' : 'stored priority score'}. Map changes only when you choose an action.` : 'No map action is suggested.'}`, [source('Candidate catalogue · temporal class, area, support and priority', true)], matches.slice(0, limit), matches.length ? [{ label: `Show ${matches.length} matches on map`, action: { type: 'query', query } }] : []);
	}
	return reply('I can explain a selected anomaly, query the current dataset by temporal class and area, rank candidates, report storage metrics, or show recorded QA and interpretation evidence. Try “Show persistent anomalies larger than 100 ha.”');
}

/** Default, offline-capable provider. No generated facts or external requests. */
export const localAssistant: AssistantProvider = {
	name: 'Local dataset assistant',
	async answer({ question, context }, signal) {
		if (signal?.aborted) throw new DOMException('Cancelled', 'AbortError');
		return answerLocal(question, context);
	},
};

/** OpenRouter-backed natural-language explanations; candidate actions stay deterministic and local. */
export function createOpenRouterAssistant(fetcher: typeof fetch = fetch): AssistantProvider {
	return {
		name: 'TerraSignal AI · OpenRouter',
		async answer(request, signal) {
			const grounded = await localAssistant.answer(request, signal);
			if (grounded.actions.length > 0 || signal?.aborted) return grounded;

			const ids = new Set(grounded.candidates.map((candidate) => candidate.id));
			const candidates = request.context.features
				.filter((feature) => ids.has(String(feature.properties.id)))
				.slice(0, 10)
				.map((feature) => feature.properties);
			const notes = Object.fromEntries([...ids]
				.filter((id) => request.context.contract.candidate_notes?.[id])
				.map((id) => [id, request.context.contract.candidate_notes![id]]));
			const { contract } = request.context;
			const evidence = {
				groundedAnswer: grounded.text,
				dataset: {
					name: contract.scene?.name,
					counts: contract.counts,
					processing: contract.processing,
					tile_processing: contract.tile_processing,
					acquisitions: contract.acquisitions,
					registration_qa: contract.registration_qa,
					t4: contract.t4,
				},
				candidates,
				projectNotes: notes,
			};
			const response = await fetcher('/api/terrasignal-chat', {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({ question: request.question, evidence }),
				signal,
			});
			if (!response.ok) {
				const result = await response.json().catch(() => null) as { error?: unknown } | null;
				throw new Error(typeof result?.error === 'string' ? result.error : 'The assistant is temporarily unavailable.');
			}
			const result = await response.json() as { reply?: unknown };
			if (typeof result.reply !== 'string' || !result.reply.trim() || result.reply.length > 12000) throw new Error('The assistant returned an invalid reply.');
			return { ...grounded, text: result.reply.trim() };
		},
	};
}

export const openRouterAssistant = createOpenRouterAssistant();
