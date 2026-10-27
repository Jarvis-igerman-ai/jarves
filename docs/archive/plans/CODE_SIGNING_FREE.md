# Free / OSS Windows code signing for the Inno Setup installer (#17)

Status: research, 2026-10-20. Verify prices and rules on the vendor pages before acting.

## Options
| Option | Cost | Eligibility | Fit for JARVIS |
|---|---|---|---|
| SignPath Foundation (OSS) | Free | OSI-approved license, no commercial dual-licensing, no proprietary components, no PUPs, actively maintained, already released, public source, automated CI build, published code signing policy page | Only if the repo is public and OSI-licensed. A private repo does NOT qualify. |
| Azure Artifact Signing (ex Trusted Signing) | Basic about 9.99 USD/mo (5,000 signatures), Premium about 99.99 USD/mo | Organizations in USA, Canada, EU, UK; individuals only USA/Canada. Identity validation takes days. Repo visibility is irrelevant (cloud signing API) | Works for a private repo if the owner is eligible; not free; an individual outside USA/Canada is excluded. |
| Self-signed / private CA cert | Free | Anyone | No SmartScreen reputation; fine only for own machines (import cert as trusted). Not for distribution. |
| Commercial OV/EV cert | roughly 200-500 USD/yr | Anyone with validated identity | Fallback. |
| No signing | Free | - | SmartScreen warning ("More info > Run anyway"). Acceptable for a personal install. |

## Private repo
- SignPath Foundation: not eligible while private. Either open-source the repo (or a public split of the installer/app) or use SignPath's paid plans.
- Azure Artifact Signing: no repo requirement; sign from any CI or local machine with signtool plus the Artifact Signing dlib, or the GitHub Action.
- Personal use (single owner, own PC/Pi): unsigned or self-signed is the pragmatic choice.

## Steps
SignPath Foundation (if repo made public):
1. Add an OSI license, a "Code Signing Policy" page/README section, and roles/privacy statement.
2. Build releases in GitHub Actions (Inno Setup `iscc`, upload artifact).
3. Apply at signpath.org; after approval add the SignPath GitHub Action; an approver signs off each release.
4. The certificate publisher is the SignPath Foundation, not the project owner.

Azure Artifact Signing:
1. Azure subscription; create an Artifact Signing account and complete identity validation.
2. Create a certificate profile; give a service principal / OIDC identity the "Signer" role.
3. In CI use the Artifact Signing GitHub Action (or signtool with the dlib). In the .iss use `SignTool=` with a command wrapper so setup and uninstaller are both signed.
4. Always timestamp.

## Recommendation
Repo is private and the project personal: ship unsigned/self-signed now. If distribution widens, use Azure Artifact Signing if the owner qualifies by location (check first), else open-source the installer and apply to SignPath Foundation. SmartScreen reputation still builds over time even with valid signatures.

## Sources
- https://signpath.org/terms , https://about.signpath.io/product/open-source
- https://learn.microsoft.com/en-us/windows/apps/package-and-deploy/code-signing-options
- https://azure.microsoft.com/en-au/products/artifact-signing/
- https://devclass.com/2026/01/14/code-signing-windows-apps-may-be-easier-and-more-secure-with-new-azure-artifact-service/
