#!/bin/sh
# Compile the kiosk's shared remoting contracts from the decompiled source, then the host, with Mono's csc.
# Mono 6.12 ships Roslyn 3.9 (C# 9), so the decompiler's file-scoped namespaces (C# 10) are turned into
# block namespaces first. Assembly names and versions come from the sources' AssemblyInfo.cs, so the types on
# the wire carry the same assembly names the kiosk uses.
set -e
OUT=/opt/remoting
mkdir -p "$OUT" /tmp/csrc

prep() {  # prep <source dir> <dest dir>
    mkdir -p "$2"
    find "$1" -name '*.cs' -not -path '*/obj/*' -not -path '*/bin/*' | while read -r f; do
        d="$2/$(echo "$f" | md5sum | cut -c1-12).cs"
        if grep -qE '^namespace [^;{]+;' "$f"; then
            sed -E 's/^namespace ([^;{]+);/namespace \1 {/' "$f" > "$d"
            printf '\n}\n' >> "$d"
        else
            cp "$f" "$d"
        fi
    done
}

prep /src/KioskShared /tmp/csrc/shared
csc -nologo -target:library -langversion:9 -nowarn:1701,1702,8019 -out:"$OUT/Redbox.Services.KioskBrokerServices.KioskShared.dll" \
    /tmp/csrc/shared/*.cs

prep /src/KioskServicesComponentModel /tmp/csrc/ksc
csc -nologo -target:library -langversion:9 -out:"$OUT/Redbox.KioskServices.ComponentModel.dll" /tmp/csrc/ksc/*.cs

CECIL=$(ls /usr/lib/mono/gac/Mono.Cecil/0.11*/Mono.Cecil.dll | head -1)
csc -nologo -target:exe -out:/tmp/PatchGenuineChannels.exe -r:"$CECIL" /src/tools/PatchGenuineChannels.cs
MONO_PATH=$(dirname "$CECIL") mono /tmp/PatchGenuineChannels.exe /src/lib/GenuineChannels.dll "$OUT/GenuineChannels.dll"
csc -nologo -target:exe -langversion:9 -out:"$OUT/RemotingHost.exe" \
    -r:"$OUT/Redbox.Services.KioskBrokerServices.KioskShared.dll" -r:"$OUT/Redbox.KioskServices.ComponentModel.dll" \
    -r:"$OUT/GenuineChannels.dll" -r:System.Runtime.Remoting.dll -r:System.Web.Extensions.dll -r:System.Web.dll \
    /src/host/*.cs
ls -la "$OUT"
