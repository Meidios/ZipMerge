# Windows PowerShell projects the system Windows.Data.Pdf renderer into .NET.
# stdin requests are zero-based page numbers. Each stdout line is one complete response.
$ErrorActionPreference = 'Stop'
try {
    Add-Type -AssemblyName System.Runtime.WindowsRuntime
    $fileType = [Windows.Storage.StorageFile, Windows.Storage, ContentType=WindowsRuntime]
    $pdfType = [Windows.Data.Pdf.PdfDocument, Windows.Data.Pdf, ContentType=WindowsRuntime]
    $optionsType = [Windows.Data.Pdf.PdfPageRenderOptions, Windows.Data.Pdf, ContentType=WindowsRuntime]
    $streamType = [Windows.Storage.Streams.InMemoryRandomAccessStream, Windows.Storage.Streams, ContentType=WindowsRuntime]
    $encoder = [Windows.Graphics.Imaging.BitmapEncoder, Windows.Graphics.Imaging, ContentType=WindowsRuntime]
    $asTask = [System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
        $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1
    }
    $operation = $asTask | Where-Object { $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' }
    $action = $asTask | Where-Object { $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncAction' }
    $file = $operation.MakeGenericMethod($fileType).Invoke($null, @($fileType::GetFileFromPathAsync($env:ZIPMERGE_PDF_PATH))).Result
    $pdf = $operation.MakeGenericMethod($pdfType).Invoke($null, @($pdfType::LoadFromFileAsync($file))).Result
    if (!$pdf -or !$pdf.PageCount) { throw 'Empty document' }
    while ($null -ne ($line = [Console]::ReadLine())) {
        $index = [uint32]::Parse($line)
        $page = $pdf.GetPage($index)
        $stream = [Activator]::CreateInstance($streamType)
        try {
            $options = [Activator]::CreateInstance($optionsType)
            $scale = [Math]::Min(1600 / $page.Size.Width, 2200 / $page.Size.Height)
            $options.DestinationWidth = [uint32][Math]::Max(1, [Math]::Round($page.Size.Width * $scale))
            $options.DestinationHeight = [uint32][Math]::Max(1, [Math]::Round($page.Size.Height * $scale))
            $options.BitmapEncoderId = $encoder::PngEncoderId
            $action.Invoke($null, @($page.RenderToStreamAsync($stream, $options))).Wait()
            $stream.Seek(0)
            $inputStream = [System.IO.WindowsRuntimeStreamExtensions]::AsStreamForRead($stream)
            $memory = New-Object System.IO.MemoryStream
            try {
                $inputStream.CopyTo($memory)
                [Console]::WriteLine(('{0} {1} {2} {3}' -f $index, $pdf.PageCount, [Math]::Ceiling($page.Size.Width), [Convert]::ToBase64String($memory.ToArray())))
            } finally {
                $memory.Dispose()
                $inputStream.Dispose()
            }
        } finally {
            $page.Dispose()
            $stream.Dispose()
        }
    }
} catch {
    [Console]::Error.WriteLine($_.Exception.GetBaseException().Message)
    exit 1
}
