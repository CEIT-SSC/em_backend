export interface CertificateRequest {
    /**
    * @maxLength 255
    */
    name: string;
}

export interface Certificate {
    /**
    * @format uuid
    */
    id?: string;
    /**
    * @maxLength 255
    */
    nameOnCertificate: string;
    fileEn?: File | null;
    fileFa?: File | null;
    isVerified?: boolean;
    /**
    * @format date-time
    */
    requestedAt?: string;
}

export interface CompletedEnrollment {
    /**
    * @label ID
    */
    id?: number;
    presentationTitle?: string;
    hasCertificate?: boolean;
    isCertificateVerified?: boolean;
}

