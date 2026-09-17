# Hosted signup email

The signup confirmation is sent by **Supabase Auth**, not Communications.
Deploying backend/frontend code does not change that hosted template.

In the production Supabase project, open **Authentication → Email Templates →
Confirm signup**. Preserve the existing subject and redirect configuration, and
replace the body with `confirm-signup.html`. Save, then send a test confirmation
to an address controlled by Admin. The action must still use Supabase's
`{{ .ConfirmationURL }}` rather than a hardcoded redirect/token.

For a minimal edit to the current hosted design, retain its markup and add
`color: #ffffff !important; -webkit-text-fill-color: #ffffff; text-decoration:
none !important` to the confirmation anchor's **inline style**, then wrap its
label in a span with the same white color. Do not rely only on a CSS class in the
head: email clients can drop it or override link color.

The template is versioned here so the production configuration can be audited.
Its existence in Git is not confirmation that the Supabase setting was updated.
