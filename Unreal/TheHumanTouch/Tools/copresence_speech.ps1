param(
    [Parameter(Mandatory=$true)][string]$RequestFile,
    [Parameter(Mandatory=$true)][string]$OutputDirectory
)
$ErrorActionPreference = 'Stop'
$request = Get-Content -LiteralPath $RequestFile -Raw -Encoding UTF8 | ConvertFrom-Json
if (-not ($request.text -is [string]) -or $request.text.Length -gt 4000 -or -not $request.text.Trim()) {
    throw 'Speech text must contain 1–4000 characters.'
}
New-Item -ItemType Directory -Path $OutputDirectory -Force | Out-Null
$output = (Resolve-Path -LiteralPath $OutputDirectory).Path
Add-Type -AssemblyName System.Speech
Add-Type -ReferencedAssemblies System.Speech -TypeDefinition @'
using System;
using System.Collections.Generic;
using System.Speech.Synthesis;
public class CoPresenceViseme {
    public double time;
    public double duration;
    public int id;
}
public class CoPresenceSpeech {
    public static CoPresenceViseme[] Render(string text, string path) {
        var cues = new List<CoPresenceViseme>();
        using (var synth = new SpeechSynthesizer()) {
            synth.SelectVoiceByHints(VoiceGender.Male, VoiceAge.Adult, 0,
                System.Globalization.CultureInfo.GetCultureInfo("en-US"));
            synth.VisemeReached += (sender, e) => {
                lock(cues) { cues.Add(new CoPresenceViseme {
                    time=e.AudioPosition.TotalSeconds,
                    duration=e.Duration.TotalSeconds, id=e.Viseme
                }); }
            };
            synth.Rate = 0;
            synth.SetOutputToWaveFile(path, new System.Speech.AudioFormat.SpeechAudioFormatInfo(
                16000, System.Speech.AudioFormat.AudioBitsPerSample.Sixteen,
                System.Speech.AudioFormat.AudioChannel.Mono));
            synth.Speak(text);
        }
        return cues.ToArray();
    }
}
'@
$cues = [CoPresenceSpeech]::Render($request.text, (Join-Path $output 'speech.wav'))
@{ version=1; visemes=@($cues) } | ConvertTo-Json -Depth 5 |
    Set-Content -LiteralPath (Join-Path $output 'visemes.json') -Encoding UTF8
Write-Output "Speech and viseme cues rendered."
