<script lang="ts">
	import { FileUpload } from '@skeletonlabs/skeleton-svelte';
	import type { FileUploadRootProps } from '@skeletonlabs/skeleton-svelte';
	import { X } from '@lucide/svelte/icons';
	import * as XLSX from 'xlsx';

	type Props = {
		fileName: string | null;
		onFileParsed: (detail: { fileName: string; data: any[][]; file: File }) => void;
		onReset: () => void;
	};
	let { fileName, onFileParsed, onReset }: Props = $props();

	const onFileChange: FileUploadRootProps['onFileChange'] = (e) => {
		if (e.acceptedFiles && e.acceptedFiles.length > 0) {
			parseFile(e.acceptedFiles[0]);
		}
	};

	function parseFile(file: File) {
		const reader = new FileReader();
		reader.onload = (e) => {
			const arrayBuffer = e.target?.result;
			if (arrayBuffer) {
				const workbook = XLSX.read(arrayBuffer, { type: 'array' });
				const sheetName = workbook.SheetNames[0];
				const worksheet = workbook.Sheets[sheetName];
				const rawData: any[][] = XLSX.utils.sheet_to_json(worksheet, { header: 1 });
				onFileParsed({ fileName: file.name, data: rawData, file });
			}
		};
		reader.readAsArrayBuffer(file);
	}
</script>

<h3 class="h4">Upload Targets</h3>
<FileUpload accept=".xlsx,.xls,.csv" {onFileChange}>
	<FileUpload.Label>Company Data File</FileUpload.Label>
	<FileUpload.Dropzone>
		<FileUpload.Trigger>Browse Files</FileUpload.Trigger>
		<FileUpload.HiddenInput />
	</FileUpload.Dropzone>
	<FileUpload.ItemGroup>
		<FileUpload.Context>
			{#snippet children(fileUpload)}
				{#if fileName}
					<div class="flex items-center space-x-4 rtl:space-x-reverse">
						<div class="min-w-0 flex-1">
							<p class="text-token truncate text-sm font-medium">
								{fileName}
							</p>
						</div>
						<div class="text-token inline-flex items-center text-base font-semibold">
							<button type="button" class="btn-icon btn-icon-sm" onclick={onReset}>
								<X class="h-4 w-4" />
							</button>
						</div>
					</div>
				{/if}
			{/snippet}
		</FileUpload.Context>
	</FileUpload.ItemGroup>
</FileUpload>
