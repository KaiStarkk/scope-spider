<script lang="ts">
	import { runState } from '$lib/shared/stores/run.svelte';
	import { browser } from '$app/environment';
	import { toaster } from '$lib/shared/stores/toast';
	import { Listbox, useListCollection } from '@skeletonlabs/skeleton-svelte';
	import { Trash2Icon } from '@lucide/svelte/icons';

	let projects = $state<string[]>([]);
	let selectedValue = $state<string[]>([]); // For bind:value
	let newProjectName = $state('');
	let isLoading = $state(true);

	const collection = $derived(
		useListCollection({
			items: projects.map((p) => ({ label: p, value: p })),
			itemToString: (item) => item.label,
			itemToValue: (item) => item.value
		})
	);

	$effect(() => {
		async function fetchProjects() {
			if (!browser) return;
			isLoading = true;
			try {
				const response = await fetch('/api/projects/list');
				if (!response.ok) {
					throw new Error('Failed to fetch projects.');
				}
				const data = await response.json();
				projects = data.projects.sort();
			} catch (error) {
				const message = error instanceof Error ? error.message : 'An unknown error occurred.';
				toaster.error({ title: 'Error', description: message });
			} finally {
				isLoading = false;
			}
		}
		fetchProjects();
	});

	async function handleSelectProject() {
		const project = selectedValue[0];
		if (project) {
			await runState.loadRun(project);
			runState.setStepValidity(0, true);
			// Do not auto-advance on resume; let the user click Next
			toaster.success({
				title: 'Project Loaded',
				description: `Resuming project: ${project}`
			});
		}
	}

	async function handleCreateProject() {
		const projectName = newProjectName.trim();
		if (projectName) {
			if (!/^[a-zA-Z0-9_-]+$/.test(projectName)) {
				toaster.error({
					title: 'Invalid Name',
					description: 'Project name can only contain letters, numbers, hyphens, and underscores.'
				});
				return;
			}
			try {
				const response = await fetch('/api/projects/create', {
					method: 'POST',
					headers: {
						'Content-Type': 'application/json'
					},
					body: JSON.stringify({ project_name: projectName })
				});

				if (!response.ok) {
					const errorData = await response.json();
					throw new Error(errorData.detail || 'Failed to create project.');
				}

                projects = [...projects, projectName].sort();
                selectedValue = [projectName];
                await runState.loadRun(projectName);
				runState.setStepValidity(0, true);
				runState.nextStep();
				newProjectName = '';
			} catch (error) {
				const message = error instanceof Error ? error.message : 'An unknown error occurred.';
				toaster.error({ title: 'Error', description: message });
			}
		}
	}

	async function handleDeleteProject(projectName: string) {
		try {
			const response = await fetch(
				`/api/projects/delete?project_name=${encodeURIComponent(projectName)}`,
				{
					method: 'DELETE'
				}
			);

			if (!response.ok) {
				const errorData = await response.json();
				throw new Error(errorData.detail || 'Failed to. delete project.');
			}

			projects = projects.filter((p) => p !== projectName);
			toaster.success({
				title: 'Project Deleted',
				description: `Project '${projectName}' has been deleted.`
			});

			if (runState.state.projectName === projectName) {
				runState.newRun(null);
				selectedValue = [];
			}
		} catch (error) {
			const message = error instanceof Error ? error.message : 'An unknown error occurred.';
			toaster.error({ title: 'Error', description: message });
		}
	}
</script>

<div class="card preset-tonal space-y-4 p-4">
	<h3 class="h3">Select or Create a Project</h3>

	<div class="grid grid-cols-1 gap-6 md:grid-cols-2">
		<!-- New Project -->
		<div class="space-y-4">
			<h4 class="h4">Create New Project</h4>
			<div class="flex flex-col gap-2">
				<input
					class="input"
					type="text"
					bind:value={newProjectName}
					placeholder="new-project-name"
				/>
				<button class="btn preset-filled mx-auto" onclick={handleCreateProject}
					>Create Project</button
				>
			</div>
		</div>

		<!-- Existing Projects -->
		<div class="space-y-4">
			<h4 class="h4">Resume Existing Project</h4>
			{#if isLoading}
				<p>Loading projects...</p>
			{:else if projects.length === 0}
				<p>No existing projects found.</p>
			{:else}
				<div class="flex flex-col gap-2">
					<Listbox
						class="w-full"
						{collection}
						value={selectedValue}
						onValueChange={(e) => (selectedValue = e.value)}
					>
						<Listbox.Label class="label w-full text-left">
							{selectedValue[0] ?? 'Select a project'}
						</Listbox.Label>
						<Listbox.Content class="max-h-64 overflow-y-auto">
							{#each collection.items as item (item.value)}
								<Listbox.Item {item} class="flex items-center justify-between">
									<Listbox.ItemText>{item.label}</Listbox.ItemText>
									<div class="flex items-center space-x-2">
										<button
											class="btn btn-sm btn-icon"
											onclick={(e) => {
												e.stopPropagation();
												handleDeleteProject(item.value);
											}}
										>
											<Trash2Icon class="h-4 w-4" />
										</button>
										<Listbox.ItemIndicator />
									</div>
								</Listbox.Item>
							{/each}
						</Listbox.Content>
					</Listbox>
					<button
						class="btn preset-filled"
						disabled={selectedValue.length === 0}
						onclick={handleSelectProject}>Resume Project</button
					>
				</div>
			{/if}
		</div>
	</div>
</div>
