<script lang="ts">
	import { browser } from '$app/environment';
	import { page } from '$app/stores';
	import { AppBar } from '@skeletonlabs/skeleton-svelte';
	import type { Snippet } from 'svelte';
	import '../app.css';

	const links = [
		{ href: '/dashboard', label: 'Dashboard' },
		{ href: '/verification', label: 'Verification' }
	];

const { children } = $props<{ children?: Snippet }>();

	$effect(() => {
		if (browser) {
			document.title = 'Scope Spider';
		}
	});

	function isActive(path: string): boolean {
		return $page.url.pathname === path || $page.url.pathname.startsWith(`${path}/`);
	}
</script>

<AppBar>
	<AppBar.Toolbar class="grid-cols-[auto_1fr] space-x-4">
		<AppBar.Headline>
			<strong>Scope Spider</strong>
		</AppBar.Headline>
	</AppBar.Toolbar>
</AppBar>

<nav class="border-b border-surface-300 bg-surface-100">
	<ul class="mx-auto flex max-w-5xl gap-6 px-6 py-3">
		{#each links as link}
			<li>
				<a
					class={`text-sm font-semibold uppercase tracking-wide ${isActive(link.href) ? 'text-primary-500' : 'text-surface-500 hover:text-primary-400'}`}
					href={link.href}
				>
					{link.label}
				</a>
			</li>
		{/each}
	</ul>
</nav>

<main class="mx-auto w-full min-h-[calc(100vh-8rem)] px-6 py-10 bg-slate-50 lg:max-w-[80vw]">
	{@render children?.()}
</main>

<footer class="border-t border-surface-200 py-4 text-center text-xs text-surface-500">
	<p>&copy; {new Date().getFullYear()} Scope Spider</p>
</footer>
