import re
import dns.resolver
import socket
import smtplib

# -----------------------------
# 1. Email format validation
# -----------------------------
def is_valid_format(email):
    regex = r"(^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$)"
    return re.match(regex, email) is not None


def get_domain(email):
    return email.split("@")[1]


# -----------------------------
# 2. Domain existence check
# -----------------------------
def domain_exists(domain):
    try:
        socket.gethostbyname(domain)
        return True
    except socket.gaierror:
        return False


# -----------------------------
# 3. MX record check
# -----------------------------
def get_mx_records(domain):
    try:
        records = dns.resolver.resolve(domain, "MX")
        return sorted(
            [(r.preference, str(r.exchange)) for r in records],
            key=lambda x: x[0]
        )
    except Exception:
        return []


# -----------------------------
# 4. SMTP mailbox verification
# -----------------------------
def smtp_mailbox_exists(email, mx_records, timeout=10):
    from_address = "verify@example.com"

    for _, mx_host in mx_records:
        try:
            server = smtplib.SMTP(mx_host, 25, timeout=timeout)
            server.helo("example.com")
            server.mail(from_address)
            code, message = server.rcpt(email)
            server.quit()

            # 250 or 251 means mailbox accepted
            if code in (250, 251):
                return True
            else:
                return False

        except (smtplib.SMTPConnectError,
                smtplib.SMTPServerDisconnected,
                smtplib.SMTPRecipientsRefused,
                socket.timeout):
            continue

    return False


# -----------------------------
# 5. Main verification function
# -----------------------------
def verify_email(email, check_smtp=True):
    result = {
        "email": email,
        "format_valid": False,
        "domain_exists": False,
        "mx_record_exists": False,
        "smtp_mailbox_exists": None,
        "is_valid_email": False
    }

    # Format check
    if not is_valid_format(email):
        return result
    result["format_valid"] = True

    domain = get_domain(email)

    # Domain check
    if not domain_exists(domain):
        return result
    result["domain_exists"] = True

    # MX check
    mx_records = get_mx_records(domain)
    if not mx_records:
        return result
    result["mx_record_exists"] = True

    # SMTP mailbox check (optional)
    if check_smtp:
        result["smtp_mailbox_exists"] = smtp_mailbox_exists(email, mx_records)
        result["is_valid_email"] = result["smtp_mailbox_exists"]
    else:
        result["is_valid_email"] = True

    return result