import requests
import random
import time
import copy
import pandas as pd
from datetime import datetime
from requests.adapters import HTTPAdapter
from pymongo import MongoClient

from common.scrawer_tools import WHOIS_URL, ERROR_SLEEP, API_KEYS, USER_AGENTS, HEADERS
from common.scrawer_tools import get_proxy_from_redis
from common.mongodb_op import query_mongodb_by_body, save_domain_subdomains2mongodb
from common.mongodb_op import MAL_DOMS_MONGO_DB, MAL_DOMS_MONGO_INDEX, DOMAIN_IP_RESOLUTION_MONGO_INDEX, \
    DOMAIN_WHOIS_MONGO_INDEX, GOOD_DOMAINS_MONGO_DB, GOOD_DOMAINS_MONGO_INDEX, DOMAIN_WHOIS_MONGO_INDEX, \
    DOMAIN_SUBDOMAIN_MONGO_INDEX, DOMAIN_IP_RESOLUTION_MONGO_INDEX
from common.mongodb_op import mongo_url
from common.common_whois_fields import CREATE_DATE, UPDATE_DATE, EXPIRY_DATE, VALID_DURATION, REGISTRANT_COUNTRY, \
    ADMIN_COUNTRY, ADMIN_REGION, CATEGORIES
from common.mongo_common import DOMAIN_2ND_FIELD, SUBDOMAINS_FIELD
from common.date_op import change_date_str_format_v1, format_date_string, differate_one_day_more
from common.domains_op import keep_3th_dom_name

client = MongoClient(mongo_url)
db_subdomain_bad = client[MAL_DOMS_MONGO_DB]
db_subdomain_good = client[GOOD_DOMAINS_MONGO_DB]
db_ip_good = client[GOOD_DOMAINS_MONGO_DB]
db_ip_bad = client[MAL_DOMS_MONGO_DB]
db_whois_good = client[GOOD_DOMAINS_MONGO_DB]
db_whois_bad = client[MAL_DOMS_MONGO_DB]
db_whois_dict = {0: db_whois_good, 1: db_whois_bad}
db_ip_dict = {0: db_ip_good, 1: db_ip_bad}
db_subdomain_dict = {0: db_subdomain_good, 1: db_subdomain_bad}

# 三个公共的集合名
ip_mongo_index = DOMAIN_IP_RESOLUTION_MONGO_INDEX
subdomain_mongo_index = DOMAIN_SUBDOMAIN_MONGO_INDEX
whois_mongo_index = DOMAIN_WHOIS_MONGO_INDEX

WHOIS_DAYS_GAP_FILE = "days_gap.txt"
ALIVE_DAYS = "alive_days"
UPDATE_DAYS = "update_days"


def get_whois_info(domain):
    """
    :param domain: 待检测的域名
    :return: 返回一个dict domain:被检测的域名 flag:该域名是否是恶意的
    """
    key_index = random.choice(range(0, len(API_KEYS)))
    api_key = API_KEYS[key_index]
    params = {"domain": domain, "apikey": api_key}

    while True:
        pro = get_proxy_from_redis()
        try:
            proxy = {'http': 'http://' + pro}
            user_agent = random.choice(USER_AGENTS)
            headers = copy.deepcopy(HEADERS)
            headers["User-Agent"] = user_agent
            s = requests.Session()
            s.mount('https://', HTTPAdapter(max_retries=1))
            s.keep_alive = False
            response = s.get(WHOIS_URL, params=params, headers=headers, timeout=1, proxies=proxy)
            # print(response.status_code)
            if response.status_code != 200:
                time.sleep(ERROR_SLEEP)
                return False
            print("pro: %s, url: %s, successfully get domain_name: %s" % (pro, response.url, domain))
            d = response.json()
            response.close()
            return d
        except Exception as e:
            # write_error_domains(domain)
            print("domain_name: %s, error: %s, pro: %s" % (domain, e, pro))
            time.sleep(ERROR_SLEEP)


def save_whois_info2mongodb(domain, whois_info, db, mongo_index=DOMAIN_WHOIS_MONGO_INDEX):
    """
    将whois信息插入到MongoDB数据库中，只查一次，若某个域名的whois信息已经存在，则不继续查询
    """
    query_body = {"domain": domain}
    if not db[mongo_index].find(query_body).count():
        db[mongo_index].insert(whois_info)


def get_old_whois_info(domain_bad, mongo_index=DOMAIN_WHOIS_MONGO_INDEX):
    """
    正常域名和恶意域名都有DOMAIN_WHOIS_MONGO_INDEX：domain_whois集合
    """
    rec = db_whois_dict[domain_bad][mongo_index].find()
    domain_set = set()
    for item in rec:
        domain = item["domain"]
        domain_set.add(domain)
    return domain_set


def save_domain_ip_resolutions2mongodb(domain, ips, db, mongo_index=DOMAIN_IP_RESOLUTION_MONGO_INDEX):
    # 如何做到不适用for循环一次向一个数组中添加多个元素: addtoset与each结合
    db[mongo_index].update({"domain": domain}, {"$addToSet": {"ips": {"$each": ips}}}, True)


def set_categories(domain_info):
    """
    从domain_info字典中取出域名对应的categories
    :param domain_info:
    :return:
    """
    bitdefender_category = domain_info.get("BitDefender category", None)  # 网站类别，如portals为门户网站
    alexa_category = domain_info.get("Alexa category", "")
    trend_micro_category = domain_info.get("TrendMicro category", None)
    categories = []
    if bitdefender_category:
        categories.append(bitdefender_category)
    if alexa_category:
        categories.append(alexa_category)
    if trend_micro_category:
        categories.append(trend_micro_category)
    return categories


def set_whois_info_dict(domain, whois_sentence, categories):
    """
    :param whois_sentence: str，是获取到的whois文本
    :param categories: 域名的categories
    :return:
    """
    print("type of whois_info : %s" % type(whois_sentence))
    if whois_sentence:
        whois_list = whois_sentence.split("\n")
        whois_dict = {item.split(":")[0]: ''.join(item.split(":")[1:]) for item in whois_list}
        create_date = whois_dict.get("Creation Date", None)  # 注册日期
        update_date = whois_dict.get("Updated Date", None)  # 更新日期
        expiry_date = whois_dict.get("Expiry Date", None)  # 过期日期
        registrant_country = whois_dict.get("Registrant Country", None)  # 注册国家
        admin_country = whois_dict.get("Admin Country", "")  # 管理国家
        admin_region = whois_dict.get("Admin State/Province", "")  # state或者province

        whois_info = {DOMAIN_2ND_FIELD: domain}
        if create_date:
            whois_info[CREATE_DATE] = change_date_str_format_v1(create_date)
        if update_date:
            whois_info[UPDATE_DATE] = change_date_str_format_v1(update_date)
        if expiry_date:
            whois_info[EXPIRY_DATE] = change_date_str_format_v1(expiry_date)
        if registrant_country:
            whois_info[REGISTRANT_COUNTRY] = registrant_country.lower()
        if admin_country:
            whois_info[ADMIN_COUNTRY] = admin_country.lower()
        if admin_region:
            whois_info[ADMIN_REGION] = admin_region.lower()
        if categories:
            whois_info[CATEGORIES] = categories
        return whois_info
    return {}


def save_subdomain_and_ip2database(domain, domain_info, domain_bad):
    db_ip = db_ip_dict[domain_bad]
    db_subdomain = db_subdomain_dict[domain_bad]
    subdomains = domain_info.get(SUBDOMAINS_FIELD, [])  # 子域名
    resolution_ips = [keep_3th_dom_name(item.get("ip_address")) for item in domain_info.get("resolutions", [])]
    if subdomains:
        save_domain_subdomains2mongodb(domain, subdomains, db_subdomain, subdomain_mongo_index)
    if resolution_ips:
        save_domain_ip_resolutions2mongodb(domain, resolution_ips, db_ip, ip_mongo_index)


def save_whois_info2database(domain, domain_info, domain_bad):
    """将域名的whois信息存入mongodb中"""
    db_whois = db_whois_dict[domain_bad]
    categories = set_categories(domain_info)
    whois_info = domain_info["whois"]
    whois_info_dict = set_whois_info_dict(domain, whois_info, categories)
    if len(whois_info_dict) > 1:  # 只有至少找到了与该域名相关的某个信息，如create_date之后才插入数据库
        print("=============================================================================")
        print("whois_info_dict: ", whois_info_dict)
        print("=============================================================================")
        save_whois_info2mongodb(domain, whois_info_dict, db_whois, whois_mongo_index)


def resolve_whois_info(domain, domain_bad):
    """
    正常域名和恶意域名都有：domain_ips，domain_subdomains，domain_whois这三个集合
    """
    domain_info = get_whois_info(domain)
    assert isinstance(domain_info, dict)  # 请求结果可能为False
    if domain_info["response_code"] == 0:  # 请求成功，但是域名不在virustotal数据库中
        print("%s" % (domain_info["verbose_msg"]))
        return
    save_whois_info2database(domain, domain_info, domain_bad)
    # save_subdomain_and_ip2database(domain, domain_info, domain_bad)


def get_all_domains(domain_bad):
    """
    从MongoDB中取出所有需要查询的域名
    :param domain_bad:
    :return:
    """
    if domain_bad:  # 取出mongodb中所有的恶意域名
        domain_list = query_mongodb_by_body(client, MAL_DOMS_MONGO_DB, MAL_DOMS_MONGO_INDEX, fields)
    else:  # 从mongodb中取出所有的正常域名
        domain_list = query_mongodb_by_body(client, GOOD_DOMAINS_MONGO_DB, GOOD_DOMAINS_MONGO_INDEX, fields)
    return domain_list


def days_gap2csv(data_dict, columns, domain_bad):
    df = pd.DataFrame(data_dict, columns=columns)
    file = str(domain_bad) + WHOIS_DAYS_GAP_FILE
    df.to_csv(file, index=True)


def count_alive_days(domain_list, domain_bad):
    db = db_whois_dict[domain_bad]
    create_days_list, update_days_list = [], []
    for domain in domain_list:
        query_body = {DOMAIN_2ND_FIELD: domain}
        recs = db[whois_mongo_index].find(query_body)
        if recs.count() > 0:
            rec = recs[0]
            time_format = "%Y%m%d"
            today = datetime.now().strftime(time_format)
            create_date = rec.get(CREATE_DATE, None)
            create_date = format_date_string(create_date) if create_date else today
            update_date = rec.get(UPDATE_DATE, None)
            update_date = format_date_string(update_date) if update_date else today
            print("create: ", create_date, " today: ", today, " update: ", update_date)
            days_gap1 = differate_one_day_more(create_date, today) + 1
            days_gap2 = differate_one_day_more(update_date, today) + 1
            print("days_gap1:%s, days_gap2: %s" % (days_gap1, days_gap2))
            create_days_list.append(days_gap1)
            update_days_list.append(days_gap2)
    data_dict = {ALIVE_DAYS: create_days_list, UPDATE_DAYS: update_days_list}
    columns = [ALIVE_DAYS, UPDATE_DAYS]
    days_gap2csv(data_dict, columns, domain_bad)


if __name__ == "__main__":
    domain_bad = int(input("please a number: 0 for collect whois of good domains, 1 for collect whois of bad domains"))
    fields = [DOMAIN_2ND_FIELD]

    # domain_list = get_all_domains(domain_bad)

    # 临时直接查询所有可以形成时间序列的域名
    from time_features.extract_time_seq2csv import get_visited_domains
    domain_list = get_visited_domains(domain_bad)

    # count_alive_days(domain_list, domain_bad)

    domain_old_set = get_old_whois_info(domain_bad)  # 剔除已经获取了whois信息的域名
    domain_list = list(set(domain_list) - domain_old_set)
    print("len of domain_list: %s" % (len(domain_list, )))
    for iter, domain in enumerate(domain_list):
        print("handlering %s domain: %s" % (iter, domain))
        try:
            if domain_bad:
                resolve_whois_info(domain, domain_bad)
            else:
                resolve_whois_info(domain, domain_bad)
        except AssertionError as e:
            print("AssertionError: %s" % (e,))
