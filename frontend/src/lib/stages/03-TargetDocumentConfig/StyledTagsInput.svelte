<script lang="ts">
	import { TagsInput } from '@skeletonlabs/skeleton-svelte';

	let {
		value = $bindable(),
		placeholder = 'Add a term...',
		name,
		label = ''
	}: {
		value: string[];
		placeholder?: string;
		name: string;
		label?: string;
	} = $props();

	function getTagClass(term: string): string {
		if (term.startsWith('+') && term.length > 1) {
			return 'text-white bg-green-500';
		}
		if (term.startsWith('~"') && term.endsWith('"') && term.length > 3) {
			return 'text-white bg-purple-500';
		}
		if (term.startsWith('"') && term.endsWith('"')) {
			return 'text-white bg-blue-500';
		}
		if (term.startsWith('-') && term.length > 1) {
			return 'text-white bg-red-500';
		}
		return '';
	}

	function handleValueChange(detail: { value: string[] } | undefined) {
		if (detail) {
			value = detail.value.map((term) => {
				if (
					term.includes(' ') &&
					!term.startsWith('"') &&
					!term.endsWith('"') &&
					!term.startsWith('~"')
				) {
					return `"${term}"`;
				}
				return term;
			});
		}
	}
</script>

<TagsInput {value} onValueChange={handleValueChange}>
	{#if label}
		<TagsInput.Label>{label}</TagsInput.Label>
	{/if}
	<TagsInput.Control>
		<TagsInput.Context>
			{#snippet children(tagsInput)}
				{#each tagsInput().value as v, index (index)}
					<TagsInput.Item value={v} {index}>
						<TagsInput.ItemPreview class={getTagClass(v)}>
							<TagsInput.ItemText>{v}</TagsInput.ItemText>
							<TagsInput.ItemDeleteTrigger />
						</TagsInput.ItemPreview>
						<TagsInput.ItemInput />
					</TagsInput.Item>
				{/each}
			{/snippet}
		</TagsInput.Context>
		<TagsInput.Input {placeholder} />
	</TagsInput.Control>
	<TagsInput.HiddenInput {name} />
</TagsInput>
