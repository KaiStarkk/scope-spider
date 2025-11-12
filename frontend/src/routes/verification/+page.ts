import { error } from '@sveltejs/kit';

type NextResponse = {
	key: string | null;
};

type OptionResponse = {
	methods?: string[];
};

export const load = async ({ fetch }) => {
	const [nextRes, optionsRes] = await Promise.all([
		fetch('/api/verification/next'),
		fetch('/api/verification/options')
	]);

	if (!nextRes.ok) {
		throw error(nextRes.status, 'Failed to load verification queue.');
	}
	const { key }: NextResponse = await nextRes.json();

	let company: Record<string, unknown> | null = null;
	if (key) {
		const detailRes = await fetch(`/api/verification/${encodeURIComponent(key)}`);
		if (!detailRes.ok) {
			throw error(detailRes.status, 'Failed to load verification target.');
		}
		company = (await detailRes.json()) as Record<string, unknown>;
	}

	let methods: string[] = [];
	if (optionsRes.ok) {
		const payload = (await optionsRes.json()) as OptionResponse;
		methods = Array.isArray(payload.methods) ? payload.methods : [];
	}

	return {
		initialKey: key,
		initialCompany: company,
		methodOptions: methods
	};
};
