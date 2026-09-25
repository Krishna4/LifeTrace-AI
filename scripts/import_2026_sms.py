#!/usr/bin/env python3
"""
Bulk import 2026 financial transactions from Android SMS XML backup into Cloudflare D1.
"""
import os
import re
import sys
import xml.etree.ElementTree as ET
from datetime import datetime
from collections import Counter
import subprocess

XML_PATH = os.path.expanduser('~/Downloads/sms-20260925212124.xml')
OUTPUT_SQL_PATH = '/Users/muralidupati/PersonalRag/cloudflare/import_2026_batch.sql'

SKIP_PATTERNS = re.compile(
    r'\b(otp|one time password|verification code|secret code|auth code|e-stmt|statement for|bill generated|'
    r'total amt due|minimum amt due|is due on|due on|set for execution|scheduled for|eligible for|apply for|'
    r'pre-approved|congratulations|reward points|claim now|discount|flat rs|use code|voucher)\b',
    re.IGNORECASE
)

def extract_account(body: str) -> str:
    m = re.search(r'\b(?:a\/c|acct|card|ending with|account)\s*(?:no\.?)?\s*([xX0-9]{3,16})\b', body, re.IGNORECASE)
    if m:
        raw = m.group(0).strip()
        return raw
    return None

def extract_upi_ref(body: str) -> str:
    m = re.search(r'\b(?:UPI\s*Ref|Ref(?:\s*No)?|Txn\s*ID)[:\s]*([0-9]{6,16})\b', body, re.IGNORECASE)
    return m.group(1) if m else None

def extract_amount(body: str):
    m = re.search(r'(?:INR|RS\.?|₹)\s*([0-9]{1,3}(?:,[0-9]{2,3})+(?:\.[0-9]{1,2})?|[0-9]+(?:\.[0-9]{1,2})?)', body, re.IGNORECASE)
    if m:
        try:
            return float(m.group(1).replace(',', ''))
        except:
            return None
    return None

def classify_direction(body: str) -> str:
    cleaned = re.sub(r'\bcredit\s*card\b', 'card', body, flags=re.IGNORECASE)
    has_credit = bool(re.search(r'\b(credited|received|deposited|deposit|refund|refunded|cashback|salary|added to)\b', cleaned, re.IGNORECASE))
    has_debit = bool(re.search(r'\b(debited|spent|sent to|paid to|paid rs|paid inr|transferred to|charged|withdrawn|purchase at|txn of|used at)\b', cleaned, re.IGNORECASE))
    if has_credit and not has_debit:
        return 'CREDIT'
    elif has_debit and not has_credit:
        return 'DEBIT'
    elif has_credit and has_debit:
        if 'debited' in cleaned.lower() and re.search(r'credited to (other|beneficiary|receiver)', cleaned, re.IGNORECASE):
            return 'DEBIT'
        elif re.search(r'credited (to|in) your', cleaned, re.IGNORECASE):
            return 'CREDIT'
        return 'DEBIT'
    return 'DEBIT'

def extract_balance(body: str):
    m = re.search(r'\b(?:avail(?:able)?\s*bal(?:ance)?|avl\s*bal|bal|avl limit|limit)[:\s]*(?:inr|rs\.?|₹)?\s*([0-9]{1,3}(?:,[0-9]{2,3})+(?:\.[0-9]{1,2})?|[0-9]+(?:\.[0-9]{1,2})?)', body, re.IGNORECASE)
    if m:
        try:
            return float(m.group(1).replace(',', ''))
        except:
            return None
    return None

def extract_entity(body: str, addr: str, tx_type: str) -> str:
    # 1. UPI P2M / P2A standard tag
    m_upi = re.search(r'UPI/(?:P2M|P2A)/[0-9]+/([^/\n\r-]+)', body)
    if m_upi:
        res = m_upi.group(1).strip()
        if len(res) > 2 and not re.search(r'^(a\/c|account|card)\b', res, re.IGNORECASE):
            return res[:40]

    # 2. 'at <Merchant>'
    m_at = re.search(r'\bat\s+([A-Z0-9][A-Za-z0-9\s&.\'-]{2,35}?)(?:\s+for\s+|\s+on\s+|\s+via\s+|\s*\(|\s+ref|\.|$)', body, re.IGNORECASE)
    if m_at:
        res = m_at.group(1).strip()
        if len(res) > 2 and not re.search(r'^(a\/c|account|card)\b', res, re.IGNORECASE):
            return res[:40]

    # 3. 'to <Recipient>'
    m_to = re.search(r'\bto\s+([A-Z0-9][A-Za-z0-9\s&.\'-]{2,35}?)(?:\s+for\s+|\s+on\s+|\s+via\s+|\s*\(|\s+ref|\.|$)', body, re.IGNORECASE)
    if m_to:
        res = m_to.group(1).strip()
        if len(res) > 2 and not re.search(r'^(a\/c|account|card)\b', res, re.IGNORECASE):
            return res[:40]

    # 4. 'from <Sender>'
    m_from = re.search(r'\bfrom\s+([A-Z0-9][A-Za-z0-9\s&.\'-]{2,35}?)(?:\s+on\s+|\s+via\s+|\s*\(|\s+ref|\.|$)', body, re.IGNORECASE)
    if m_from:
        res = m_from.group(1).strip()
        if len(res) > 2 and not re.search(r'^(a\/c|account|card)\b', res, re.IGNORECASE):
            return res[:40]

    # 5. 'towards <Entity>'
    m_towards = re.search(r'\btowards\s+([A-Z0-9][A-Za-z0-9\s&.\'-]{2,35}?)(?:\s+on\s+|\s+for\s+|\s*\(|\s+ref|\.|$)', body, re.IGNORECASE)
    if m_towards:
        res = m_towards.group(1).strip()
        if len(res) > 2 and not re.search(r'^(a\/c|account|card)\b', res, re.IGNORECASE):
            return res[:40]

    # 6. 'Info: <Merchant>'
    m_info = re.search(r'\bInfo:\s*([A-Za-z0-9\s&.\'-]{2,35}?)(?:\.|\s+Avail|$)', body, re.IGNORECASE)
    if m_info:
        res = m_info.group(1).strip()
        if len(res) > 2:
            return res[:40]

    # 7. Fallback to bank/sender tag
    clean_addr = re.sub(r'^[A-Z]{2}-', '', addr).replace('-S', '')
    return clean_addr or ('General Income' if tx_type == 'CREDIT' else 'General Expense')

def escape_sql(val) -> str:
    if val is None:
        return 'NULL'
    if isinstance(val, (int, float)):
        return str(val)
    s = str(val).replace("'", "''").strip()
    return f"'{s}'"

def main():
    if not os.path.exists(XML_PATH):
        print(f"Error: {XML_PATH} not found!")
        sys.exit(1)

    print(f"Reading {XML_PATH}...")
    seen_tx = set()
    records = []
    skipped_duplicates = 0

    for event, elem in ET.iterparse(XML_PATH, events=('end',)):
        if elem.tag == 'sms' and elem.get('type') == '1':
            date_ms = int(elem.get('date', '0'))
            dt = datetime.fromtimestamp(date_ms / 1000)
            
            if dt.year == 2026:
                body = elem.get('body', '')
                addr = elem.get('address', '')
                
                if not SKIP_PATTERNS.search(body):
                    amt = extract_amount(body)
                    if amt and amt > 0:
                        tx_type = classify_direction(body)
                        acc = extract_account(body)
                        ref = extract_upi_ref(body)
                        bal = extract_balance(body)
                        entity = extract_entity(body, addr, tx_type)
                        date_str = dt.strftime('%Y-%m-%d')
                        time_bucket = dt.strftime('%Y-%m-%d %H') + f':{dt.minute // 5}'

                        # Deduplication key
                        if ref:
                            key = (ref, tx_type, amt, acc or '')
                        else:
                            key = (time_bucket, tx_type, amt, acc or '')

                        if key in seen_tx:
                            skipped_duplicates += 1
                        else:
                            seen_tx.add(key)
                            
                            notes = f"UPI Ref: {ref}" if ref else f"Bank: {addr}"
                            records.append({
                                'tx_type': tx_type,
                                'entity_person': entity,
                                'amount': amt,
                                'currency': 'INR',
                                'transaction_date': date_str,
                                'account': acc,
                                'balance': bal,
                                'notes': notes,
                                'username': 'default_user',
                                'created_at': dt.strftime('%Y-%m-%d %H:%M:%S')
                            })
        elem.clear()

    print(f"\nExtracted {len(records)} unique 2026 transactions (Filtered {skipped_duplicates} duplicate SMS alerts).")
    
    # Sort chronologically
    records.sort(key=lambda r: (r['transaction_date'], r['created_at']))

    by_type = Counter(r['tx_type'] for r in records)
    total_inflow = sum(r['amount'] for r in records if r['tx_type'] == 'CREDIT')
    total_outflow = sum(r['amount'] for r in records if r['tx_type'] == 'DEBIT')
    net_flow = total_inflow - total_outflow

    print(f"Breakdown: {dict(by_type)}")
    print(f"Total Credits (Inflow):  +INR {total_inflow:,.2f}")
    print(f"Total Debits (Outflow):  -INR {total_outflow:,.2f}")
    print(f"Net Cash Flow:           INR {net_flow:,.2f}")

    # Generate SQL batches (100 rows per batch)
    print(f"\nGenerating SQL file at {OUTPUT_SQL_PATH}...")
    batch_size = 100
    with open(OUTPUT_SQL_PATH, 'w', encoding='utf-8') as f:
        f.write("-- 2026 SMS Bulk Import for LifeTrace AI\n")
        for i in range(0, len(records), batch_size):
            chunk = records[i:i + batch_size]
            f.write("INSERT INTO transactions (tx_type, entity_person, amount, currency, transaction_date, account, balance, notes, username, created_at) VALUES\n")
            row_strs = []
            for r in chunk:
                row_str = f"({escape_sql(r['tx_type'])}, {escape_sql(r['entity_person'])}, {r['amount']}, {escape_sql(r['currency'])}, {escape_sql(r['transaction_date'])}, {escape_sql(r['account'])}, {escape_sql(r['balance'])}, {escape_sql(r['notes'])}, {escape_sql(r['username'])}, {escape_sql(r['created_at'])})"
                row_strs.append(row_str)
            f.write(",\n".join(row_strs) + ";\n\n")

    print(f"SQL file written successfully ({os.path.getsize(OUTPUT_SQL_PATH):,} bytes). Ready for execution!")

if __name__ == '__main__':
    main()
