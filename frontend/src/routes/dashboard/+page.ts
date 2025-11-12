import { error } from '@sveltejs/kit';

type StageSummary = {
	total: number;
	searched: number;
	downloaded: number;
	extracted: number;
	analysed: number;
	verified: number;
};

type DashboardResponse = {
	companies: unknown[];
	stats: {
		total: number;
		searched: number;
		downloaded: number;
		extracted: number;
		analysed: number;
		verified: number;
		pending: number;
		stages: StageSummary;
	};
	metadata: Record<string, unknown>;
};

type MetricsResponse = {
	summary: {
		total_companies: number;
		filtered_companies: number;
		stages: StageSummary;
		filtered_stages: StageSummary;
	};
	[key: string]: unknown;
};

export const load = async ({ fetch }) => {
	const [companiesRes, metricsRes] = await Promise.all([
		fetch('/api/dashboard/companies'),
		fetch('/api/dashboard/metrics')
	]);

	if (!companiesRes.ok) {
		throw error(companiesRes.status, 'Failed to load dashboard data.');
	}

	if (!metricsRes.ok) {
		throw error(metricsRes.status, 'Failed to load dashboard metrics.');
	}

	const payload = (await companiesRes.json()) as DashboardResponse;
	const metrics = (await metricsRes.json()) as MetricsResponse;
	return {
		dashboard: payload,
		metrics
	};
};
