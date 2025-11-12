import { error } from '@sveltejs/kit';

type DashboardResponse = {
	companies: unknown[];
	stats: { total: number; verified: number; pending: number };
	metadata: Record<string, unknown>;
};

type MetricsResponse = Record<string, unknown>;

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
